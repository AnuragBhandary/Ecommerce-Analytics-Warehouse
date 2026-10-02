"""End-to-end proof run on the full Olist dataset; produces the numbers in docs/RESULTS.md.

    make prove        (pause the Airflow DAG first: it would compete for the same databases)

1. Reset the source and the warehouse.
2. Replay ~26 months of business time in 30-day ticks. Each tick runs the DAG's steps in order:
   extract_load -> dbt snapshot -> dbt run -> dbt test -> reconcile. Every tick must pass.
3. In one tick, start the extract-load and SIGKILL it after it has merged orders and items but
   before commit; check that nothing moved, then let the retry run.
4. Fingerprint every mart (order-independent hash of all rows, minus load timestamps), then
   a) rebuild every model from scratch with --full-refresh: the hashes must not change
      (the incremental MERGE path equals a full rebuild), and
   b) rerun the whole pipeline with no new source data: the hashes must not change
      (idempotent reruns).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg

from olistwh import extract_load, reconcile, simulator
from olistwh.config import Settings
from olistwh.timeline import Timeline

ROOT = Path(__file__).resolve().parents[1]
DBT = shutil.which("dbt") or str(Path(sys.executable).with_name("dbt"))
MARTS = [
    "dim_customer",
    "dim_product",
    "dim_seller",
    "dim_date",
    "fct_orders",
    "fct_order_items",
    "fct_payments",
    "fct_reviews",
    "mart_seller_monthly",
    "mart_customer_cohorts",
]


def dbt(*args: str) -> tuple[float, str]:
    t = time.perf_counter()
    p = subprocess.run(
        [DBT, *args, "--no-use-colors"],
        cwd=ROOT / "warehouse",
        env={**os.environ, "DBT_PROFILES_DIR": "."},
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        print(p.stdout[-3000:])
        raise SystemExit(f"dbt {' '.join(args)} failed")
    return time.perf_counter() - t, p.stdout


def fingerprints(s: Settings) -> dict[str, str]:
    with psycopg.connect(s.pg_dsn) as pg:
        return {
            m: pg.execute(  # type: ignore[index]
                f"SELECT md5(coalesce(string_agg(h, '' ORDER BY h), '')) FROM ("  # type: ignore[arg-type]
                f" SELECT md5((to_jsonb(t) - '_loaded_at')::text) AS h FROM marts.{m} t) x"
            ).fetchone()[0]
            for m in MARTS
        }


def main() -> None:
    s = Settings.from_env()
    tl = Timeline.load(s.data_dir)
    my = s.mysql()
    simulator.reset(my)
    with psycopg.connect(s.pg_dsn, autocommit=True) as pg:
        for schema in ("raw", "staging", "snapshots", "marts"):
            pg.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")  # type: ignore[arg-type]

    ticks = list(pd.date_range(tl.start.normalize(), tl.end + pd.Timedelta(days=30), freq="30D"))
    crash_tick = len(ticks) // 2
    log: list[dict[str, object]] = []
    tests_run = 0
    for i, until in enumerate(ticks[1:], 1):
        row: dict[str, object] = {"tick": i, "until": str(until.date())}
        t = time.perf_counter()
        written = simulator.advance(my, tl, until)
        row["source_rows_written"] = sum(written.values())
        row["simulate_s"] = round(time.perf_counter() - t, 2)
        time.sleep(1.1)  # let the writes clear the EL's 1 s safety lag

        if i == crash_tick:
            with psycopg.connect(s.pg_dsn) as pg:
                wm = pg.execute("SELECT max(watermark) FROM raw._watermarks").fetchone()
                n = pg.execute("SELECT count(*) FROM raw.order_items").fetchone()
            proc = subprocess.Popen(
                [sys.executable, "-m", "olistwh", "el", "--safety-seconds", "1"],
                env={**os.environ, "OLISTWH_EL_PAUSE_AFTER": "order_items"},
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("PAUSED_AFTER"):
                    break
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait()
            with psycopg.connect(s.pg_dsn) as pg:
                assert pg.execute("SELECT max(watermark) FROM raw._watermarks").fetchone() == wm
                assert pg.execute("SELECT count(*) FROM raw.order_items").fetchone() == n
            row["crash"] = "EL SIGKILLed after merging orders + items: watermark and raw unchanged"
            print(f"tick {i}: {row['crash']}")

        t = time.perf_counter()
        res = extract_load.run(s, safety_lag=pd.Timedelta(seconds=1))
        row["el_s"] = round(time.perf_counter() - t, 2)
        row["el_applied"] = sum(res.applied.values())
        row["snapshot_s"] = round(dbt("snapshot")[0], 2)
        row["dbt_run_s"] = round(dbt("run")[0], 2)
        dt, out = dbt("test")
        row["dbt_test_s"] = round(dt, 2)
        m = re.search(r"PASS=(\d+) WARN=(\d+) ERROR=(\d+)", out)
        assert m and m.group(3) == "0", out[-2000:]
        tests_run = int(m.group(1)) + int(m.group(2))
        rep = reconcile.run(s)
        row["reconcile"] = f"{sum(c['ok'] for c in rep.checks)}/{len(rep.checks)}"
        assert rep.ok, [c for c in rep.checks if not c["ok"]]
        log.append(row)
        print(row)

    inc = fingerprints(s)
    full_s, _ = dbt("run", "--full-refresh")
    full = fingerprints(s)
    extract_load.run(s, safety_lag=pd.Timedelta(seconds=1))
    dbt("snapshot")
    rerun_s, _ = dbt("run")
    rerun = fingerprints(s)
    inc_run_s = [float(r["dbt_run_s"]) for r in log]  # type: ignore[arg-type]

    with psycopg.connect(s.pg_dsn) as pg:

        def one(q: str) -> object:
            return pg.execute(q).fetchone()[0]  # type: ignore[arg-type,index]

        stats = {
            "orders": one("SELECT count(*) FROM marts.fct_orders"),
            "order_items": one("SELECT count(*) FROM marts.fct_order_items"),
            "revenue": str(one("SELECT sum(gross_revenue) FROM marts.fct_orders")),
            "customer_versions_closed": one(
                "SELECT count(*) FROM marts.dim_customer WHERE NOT is_current"
            ),
            "customers_with_history": one(
                "SELECT count(DISTINCT customer_unique_id) FROM marts.dim_customer"
                " WHERE version_count > 1"
            ),
            "raw_rows": one(
                "SELECT sum(n) FROM (SELECT count(*) n FROM raw.orders UNION ALL"
                " SELECT count(*) FROM raw.order_items UNION ALL SELECT count(*)"
                " FROM raw.order_payments UNION ALL SELECT count(*) FROM"
                " raw.order_reviews UNION ALL SELECT count(*) FROM raw.customers"
                " UNION ALL SELECT count(*) FROM raw.products UNION ALL SELECT"
                " count(*) FROM raw.sellers) x"
            ),
        }
    true_moves = len(tl.customer_versions) - tl.customer_versions["customer_unique_id"].nunique()
    summary = {
        "ticks": len(log),
        "dbt_tests": tests_run,
        "every_tick_passed_tests_and_reconcile": True,
        "incremental_equals_full_refresh": inc == full,
        "rerun_is_idempotent": rerun == inc,
        "mismatched_marts": [m for m in MARTS if inc[m] != full[m] or inc[m] != rerun[m]],
        "dbt_run_incremental_median_s": round(float(pd.Series(inc_run_s).median()), 2),
        "dbt_run_full_refresh_s": round(full_s, 2),
        "dbt_rerun_no_new_data_s": round(rerun_s, 2),
        "el_median_s": float(pd.Series([r["el_s"] for r in log]).median()),
        "address_changes_in_source_data": true_moves,
        **stats,
    }
    out = ROOT / "docs" / "results.json"
    out.write_text(json.dumps({"summary": summary, "ticks": log}, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    assert summary["incremental_equals_full_refresh"] and summary["rerun_is_idempotent"]


if __name__ == "__main__":
    main()
