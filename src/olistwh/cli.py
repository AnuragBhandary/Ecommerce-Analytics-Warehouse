"""Command line: olistwh <command> [options]

sim init                     create the MySQL source schema
sim reset                    drop and recreate it (clock back to the start)
sim advance --days N         move business time forward N days in one tick
sim advance --until DATE     ... or up to a date
sim run --step-hours H --interval S [--forever]
                             keep ticking (the live source behind the Airflow DAG)
sim status                   show the business clock and row counts
el                           one incremental extract-load run
reconcile [--raw-only]       source vs raw vs marts; exits 1 on any mismatch
export-marts --out DIR       write the marts to CSV (for Power BI / Excel without Docker)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

from . import extract_load, reconcile, simulator
from .config import Settings
from .tables import TABLES
from .timeline import Timeline

MARTS = [
    "dim_date",
    "dim_customer",
    "dim_product",
    "dim_seller",
    "fct_orders",
    "fct_order_items",
    "fct_payments",
    "fct_reviews",
    "mart_seller_monthly",
    "mart_customer_cohorts",
]


def _sim(args: argparse.Namespace, s: Settings) -> int:
    if args.sim_cmd in ("init", "reset"):
        conn = s.mysql()
        (simulator.init_schema if args.sim_cmd == "init" else simulator.reset)(conn)
        conn.close()
        print(f"source schema ready ({args.sim_cmd})")
        return 0
    if args.sim_cmd == "status":
        conn = s.mysql()
        simulator.init_schema(conn)
        print("business clock:", simulator.clock(conn))
        with conn.cursor() as cur:
            for t in TABLES:
                cur.execute(f"SELECT count(*) FROM `{t.name}`")
                print(f"  {t.name:22s} {cur.fetchone()[0]:>8,}")  # type: ignore[index]
        conn.close()
        return 0
    if args.sim_cmd == "advance":
        tl = Timeline.load(s.data_dir)
        conn = s.mysql()
        simulator.init_schema(conn)
        now = simulator.clock(conn)
        conn.commit()
        start = max(now, tl.start - pd.Timedelta(seconds=1))
        until = pd.Timestamp(args.until) if args.until else start + pd.Timedelta(days=args.days)
        counts = simulator.advance(conn, tl, until)
        conn.close()
        print(f"clock {now} -> {until}")
        for k, v in counts.items():
            print(f"  {k:22s} {v:>8,} rows written")
        return 0
    if args.sim_cmd == "run":
        simulator.run(
            s,
            until=pd.Timestamp(args.until) if args.until else None,
            step=pd.Timedelta(hours=args.step_hours),
            interval_s=args.interval,
            stop_at_end=not args.forever,
        )
        return 0
    raise AssertionError(args.sim_cmd)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(
        prog="olistwh", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sim = sub.add_parser("sim").add_subparsers(dest="sim_cmd", required=True)
    sim.add_parser("init")
    sim.add_parser("reset")
    sim.add_parser("status")
    adv = sim.add_parser("advance")
    g = adv.add_mutually_exclusive_group(required=True)
    g.add_argument("--days", type=float)
    g.add_argument("--until")
    r = sim.add_parser("run")
    r.add_argument("--step-hours", type=float, default=24)
    r.add_argument("--interval", type=float, default=0)
    r.add_argument("--until")
    r.add_argument("--forever", action="store_true", help="keep polling after the data ends")

    el = sub.add_parser("el")
    el.add_argument("--lookback-minutes", type=float, default=10)
    el.add_argument("--safety-seconds", type=float, default=2)

    rc = sub.add_parser("reconcile")
    rc.add_argument("--raw-only", action="store_true")

    ex = sub.add_parser("export-marts")
    ex.add_argument("--out", type=Path, default=Path("exports"))

    args = p.parse_args(argv)
    s = Settings.from_env()

    if args.cmd == "sim":
        return _sim(args, s)
    if args.cmd == "el":
        res = extract_load.run(
            s,
            lookback=timedelta(minutes=args.lookback_minutes),
            safety_lag=timedelta(seconds=args.safety_seconds),
        )
        print(f"run {res.run_id}: watermark {res.watermark_from} -> {res.watermark_to}")
        print(f"  extracted {sum(res.extracted.values()):,}, applied {sum(res.applied.values()):,}")
        return 0
    if args.cmd == "reconcile":
        rep = reconcile.run(s, marts=not args.raw_only)
        failed = [c for c in rep.checks if not c["ok"]]
        print(
            f"{len(rep.checks) - len(failed)}/{len(rep.checks)} checks passed "
            f"at watermark {rep.watermark}"
        )
        return 1 if failed else 0
    if args.cmd == "export-marts":
        args.out.mkdir(parents=True, exist_ok=True)
        with s.pg() as pg:
            for m in MARTS:
                with (
                    (args.out / f"{m}.csv").open("wb") as f,
                    pg.cursor() as cur,
                    cur.copy(f"COPY marts.{m} TO STDOUT WITH (FORMAT csv, HEADER true)") as copy,
                ):
                    for chunk in copy:
                        f.write(chunk)
                print("wrote", args.out / f"{m}.csv")
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    sys.exit(main())
