"""Integration tests against real MySQL and PostgreSQL (make infra)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import psycopg
import pytest

from olistwh import cli, extract_load, reconcile, simulator
from olistwh.config import Settings
from olistwh.tables import TABLES
from olistwh.timeline import Timeline

pytestmark = pytest.mark.integration

# Tests run the EL right after writing to the source; a zero safety lag makes the rows visible
# immediately (the lag exists for long-running writers in production).
NO_LAG = timedelta(0)


def raw_state(s: Settings) -> dict[str, set[tuple]]:
    """Every raw table without the load bookkeeping columns, for comparing two loads."""
    out = {}
    with psycopg.connect(s.pg_dsn) as pg:
        for t in TABLES:
            cols = ", ".join([*t.col_names, "_src_updated_at"])
            out[t.name] = set(pg.execute(f"SELECT {cols} FROM raw.{t.name}").fetchall())  # type: ignore[arg-type]
    return out


def advance(s: Settings, tl: Timeline, until: str | pd.Timestamp) -> dict[str, int]:
    conn = s.mysql()
    try:
        return simulator.advance(conn, tl, pd.Timestamp(until))
    finally:
        conn.close()


def el(s: Settings, **kw: timedelta) -> extract_load.RunResult:
    kw.setdefault("safety_lag", NO_LAG)
    return extract_load.run(s, **kw)


def test_incremental_loads_equal_one_full_load(settings: Settings, timeline: Timeline) -> None:
    for until in pd.date_range("2017-01-01", "2018-11-01", freq="45D"):
        advance(settings, timeline, until)
        el(settings)
    advance(settings, timeline, timeline.end)
    el(settings)
    incremental = raw_state(settings)

    # Same source, loaded from scratch in one go.
    with psycopg.connect(settings.pg_dsn, autocommit=True) as pg:
        pg.execute("DROP SCHEMA raw CASCADE")
    el(settings)
    assert raw_state(settings) == incremental
    assert len(incremental["orders"]) == len(timeline.orders)


def test_rerun_is_a_no_op(settings: Settings, timeline: Timeline) -> None:
    advance(settings, timeline, "2017-08-01")
    first = el(settings)
    assert sum(first.applied.values()) > 0
    again = el(settings, lookback=timedelta(days=3650))  # re-read *everything*
    assert sum(again.extracted.values()) == sum(first.extracted.values())
    assert sum(again.applied.values()) == 0


def test_updates_overwrite_older_versions(settings: Settings, timeline: Timeline) -> None:
    advance(settings, timeline, "2017-06-01")
    el(settings)
    with psycopg.connect(settings.pg_dsn) as pg:
        before = dict(pg.execute("SELECT order_id, order_status FROM raw.orders").fetchall())
    counts = advance(settings, timeline, "2017-06-20")
    assert counts["orders"] > 0
    el(settings)
    with psycopg.connect(settings.pg_dsn) as pg:
        after = dict(pg.execute("SELECT order_id, order_status FROM raw.orders").fetchall())
    moved = [o for o in before if before[o] != after[o]]
    assert moved, "some open orders should have progressed"
    assert reconcile.run(settings, marts=False).ok


def test_lookback_catches_late_commits(settings: Settings, timeline: Timeline) -> None:
    """updated_at is stamped when the UPDATE runs, not when it commits. A transaction that commits
    after the extractor has moved past that timestamp is only caught by the lookback window."""
    advance(settings, timeline, "2017-03-01")
    el(settings)

    slow = settings.mysql()
    with slow.cursor() as cur:
        cur.execute("UPDATE orders SET order_status = 'processing' ORDER BY order_id LIMIT 1")
        cur.execute(
            "SELECT order_id FROM orders WHERE order_status = 'processing' "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        late_id = cur.fetchone()[0]  # type: ignore[index]
    time.sleep(0.05)
    mid = el(settings)  # runs while the update is still uncommitted: invisible to its snapshot
    slow.commit()
    slow.close()

    def raw_status() -> str:
        with psycopg.connect(settings.pg_dsn) as pg:
            return pg.execute(
                "SELECT order_status FROM raw.orders WHERE order_id = %s",  # type: ignore[index]
                (late_id,),
            ).fetchone()[0]

    assert raw_status() != "processing"
    # Without a lookback the row is behind the watermark forever:
    el(settings, lookback=timedelta(0))
    assert raw_status() != "processing"
    rep = reconcile.run(settings, marts=False)
    assert not rep.ok and any("stale" in c["check"] and not c["ok"] for c in rep.checks)
    # With the default lookback it's picked up:
    el(settings)
    assert raw_status() == "processing"
    assert mid.watermark_to < pd.Timestamp.now(tz="UTC").tz_localize(None)
    assert reconcile.run(settings, marts=False).ok


def test_sigkill_mid_load_leaves_no_trace(
    settings: Settings, timeline: Timeline, env: dict[str, str]
) -> None:
    advance(settings, timeline, "2017-05-01")
    el(settings)
    before = raw_state(settings)
    with psycopg.connect(settings.pg_dsn) as pg:
        wm_before = pg.execute("SELECT max(watermark) FROM raw._watermarks").fetchone()

    advance(settings, timeline, "2017-09-01")
    proc = subprocess.Popen(
        [sys.executable, "-m", "olistwh", "el", "--safety-seconds", "0"],
        env={**env, "OLISTWH_EL_PAUSE_AFTER": "order_items"},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:  # orders and order_items are merged, not committed
        if line.startswith("PAUSED_AFTER"):
            break
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()

    with psycopg.connect(settings.pg_dsn) as pg:
        assert pg.execute("SELECT max(watermark) FROM raw._watermarks").fetchone() == wm_before
    assert raw_state(settings) == before  # nothing half-loaded

    el(settings)  # the retry
    assert reconcile.run(settings, marts=False).ok


def test_reconcile_detects_tampering(settings: Settings, timeline: Timeline) -> None:
    advance(settings, timeline, "2017-04-01")
    el(settings)
    assert reconcile.run(settings, marts=False).ok
    with psycopg.connect(settings.pg_dsn) as pg:
        pg.execute(
            "DELETE FROM raw.order_items WHERE ctid IN (SELECT ctid FROM raw.order_items LIMIT 1)"
        )
        pg.execute(
            "UPDATE raw.customers SET _src_updated_at = _src_updated_at - interval '1 day'"
            " WHERE ctid IN (SELECT ctid FROM raw.customers LIMIT 1)"
        )
    failed = {c["check"] for c in reconcile.run(settings, marts=False).checks if not c["ok"]}
    assert failed == {
        "order_items: source rows due in raw",
        "customers: stale versions in raw",
        "revenue (items + freight): source vs raw",
    }


def test_cli_end_to_end_with_dbt(
    settings: Settings,
    env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The whole DAG, in order, through the CLI and dbt: two ticks, so the second dbt run is an
    incremental MERGE and the snapshot sees address changes."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    dbt = ["dbt", "--no-use-colors", "-q"]
    wh = {"cwd": "warehouse", "env": {**env, "DBT_PROFILES_DIR": "."}, "check": True}
    for until in ("2017-09-01", "2018-11-01"):
        assert cli.main(["sim", "advance", "--until", until]) == 0
        assert cli.main(["el", "--safety-seconds", "0"]) == 0
        subprocess.run([*dbt, "snapshot"], **wh)  # type: ignore[call-overload]
        subprocess.run([*dbt, "build", "--exclude", "resource_type:snapshot"], **wh)  # type: ignore[call-overload]
        assert cli.main(["reconcile"]) == 0
    assert cli.main(["sim", "status"]) == 0
    assert "business clock: 2018-11-01" in capsys.readouterr().out
    assert cli.main(["export-marts", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "fct_orders.csv").read_text().startswith("order_id,customer_sk,")

    with psycopg.connect(settings.pg_dsn) as pg:
        versions = pg.execute(
            "SELECT count(*) FILTER (WHERE NOT is_current) FROM marts.dim_customer"
        ).fetchone()[0]  # type: ignore[index]
        assert versions > 0, "the second tick should have closed some address versions"
        assert (
            pg.execute(
                "SELECT count(*) FROM marts.fct_orders WHERE customer_sk IS NULL"
            ).fetchone()[0]
            == 0
        )  # type: ignore[index]


def test_sim_run_ticks_to_target(
    settings: Settings, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert cli.main(["sim", "run", "--step-hours", "720", "--until", "2017-03-01"]) == 0
    conn = settings.mysql()
    assert simulator.clock(conn) == pd.Timestamp("2017-03-01")
    conn.close()
    assert cli.main(["sim", "advance", "--days", "10"]) == 0
    assert cli.main(["sim", "reset"]) == 0
    assert cli.main(["sim", "init"]) == 0
    conn = settings.mysql()
    assert simulator.clock(conn) < pd.Timestamp("2017-01-01")
    conn.close()
