"""Reconciliation: proves the warehouse agrees with the source, and fails the DAG if it doesn't.

Two hops are checked against the last committed watermark W:

source -> raw (row level, every table)
    missing   source rows created at or before W that are not in raw
    stale     source rows last changed at or before W whose version in raw differs
    phantom   raw rows that no longer exist in the source
  Rows changed after W are legitimately ahead of raw and only checked for presence.

raw -> marts (totals)
    fact row counts and revenue in the marts equal the same figures computed from raw.

Money is compared exactly (NUMERIC on both sides), not with a tolerance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg

from .config import Settings
from .tables import TABLES

log = logging.getLogger(__name__)


@dataclass
class Report:
    watermark: datetime
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def add(self, name: str, source: Any, target: Any, detail: str = "") -> None:
        ok = source == target
        self.checks.append(
            {
                "check": name,
                "source": str(source),
                "target": str(target),
                "ok": ok,
                "detail": detail,
            }
        )
        (log.info if ok else log.error)(
            "%-4s %-45s %s vs %s %s", "ok" if ok else "FAIL", name, source, target, detail
        )


def _source_vs_raw(settings: Settings, pg: psycopg.Connection, rep: Report) -> None:
    my = settings.mysql()
    try:
        cur = my.cursor()
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        for t in TABLES:
            pk = ", ".join(t.pk)
            cur.execute(
                f"SELECT {pk}, created_at <= %s, updated_at <= %s, updated_at FROM `{t.name}`",
                (rep.watermark, rep.watermark),
            )
            n = len(t.pk)
            src = {r[:n]: (bool(r[n]), bool(r[n + 1]), r[n + 2]) for r in cur.fetchall()}
            raw = {
                r[:n]: r[n]
                for r in pg.execute(f"SELECT {pk}, _src_updated_at FROM raw.{t.name}").fetchall()
            }
            due = {k for k, (created, _, _) in src.items() if created}
            missing = due - raw.keys()
            stale = [
                k for k, (_, settled, ver) in src.items() if settled and k in raw and raw[k] != ver
            ]
            phantom = raw.keys() - src.keys()
            rep.add(
                f"{t.name}: source rows due in raw",
                len(due),
                len(due) - len(missing),
                f"missing e.g. {sorted(missing)[:3]}" if missing else "",
            )
            rep.add(
                f"{t.name}: stale versions in raw",
                0,
                len(stale),
                f"e.g. {stale[:3]}" if stale else "",
            )
            rep.add(f"{t.name}: phantom rows in raw", 0, len(phantom))

        cur.execute(
            "SELECT COALESCE(SUM(price + freight_value), 0) FROM order_items"
            " WHERE created_at <= %s",
            (rep.watermark,),
        )
        src_gmv = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "SELECT COALESCE(SUM(payment_value), 0) FROM order_payments WHERE created_at <= %s",
            (rep.watermark,),
        )
        src_paid = cur.fetchone()[0]  # type: ignore[index]
        my.rollback()
    finally:
        my.close()

    raw_gmv, raw_paid = pg.execute(
        "SELECT (SELECT COALESCE(SUM(price + freight_value), 0) FROM raw.order_items),"
        "       (SELECT COALESCE(SUM(payment_value), 0) FROM raw.order_payments)"
    ).fetchone()  # type: ignore[misc]
    rep.add("revenue (items + freight): source vs raw", Decimal(src_gmv), raw_gmv)
    rep.add("payments: source vs raw", Decimal(src_paid), raw_paid)


def _raw_vs_marts(pg: psycopg.Connection, rep: Report) -> None:
    q = {
        "orders: raw vs fct_orders": (
            "SELECT count(*) FROM raw.orders",
            "SELECT count(*) FROM marts.fct_orders",
        ),
        "order items: raw vs fct_order_items": (
            "SELECT count(*) FROM raw.order_items",
            "SELECT count(*) FROM marts.fct_order_items",
        ),
        "revenue: raw vs fct_order_items": (
            "SELECT COALESCE(SUM(price + freight_value), 0) FROM raw.order_items",
            "SELECT COALESCE(SUM(item_revenue), 0) FROM marts.fct_order_items",
        ),
        "revenue: fct_order_items vs fct_orders": (
            "SELECT COALESCE(SUM(item_revenue), 0) FROM marts.fct_order_items",
            "SELECT COALESCE(SUM(gross_revenue), 0) FROM marts.fct_orders",
        ),
        "payments: raw vs fct_payments": (
            "SELECT COALESCE(SUM(payment_value), 0) FROM raw.order_payments",
            "SELECT COALESCE(SUM(payment_value), 0) FROM marts.fct_payments",
        ),
        "reviews: raw vs fct_reviews": (
            "SELECT count(*) FROM raw.order_reviews",
            "SELECT count(*) FROM marts.fct_reviews",
        ),
    }
    for name, (a, b) in q.items():
        rep.add(name, pg.execute(a).fetchone()[0], pg.execute(b).fetchone()[0])  # type: ignore[index]


def run(settings: Settings, marts: bool = True) -> Report:
    pg = settings.pg()
    try:
        row = pg.execute(
            "SELECT watermark_to FROM raw._el_runs ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("no extract-load run yet")
        rep = Report(row[0])
        _source_vs_raw(settings, pg, rep)
        if marts:
            _raw_vs_marts(pg, rep)
        pg.execute(
            "CREATE TABLE IF NOT EXISTS raw._reconcile_runs (run_at TIMESTAMPTZ PRIMARY KEY"
            " DEFAULT clock_timestamp(), watermark TIMESTAMP NOT NULL, ok BOOLEAN NOT NULL,"
            " checks JSONB NOT NULL)"
        )
        pg.execute(
            "INSERT INTO raw._reconcile_runs (watermark, ok, checks) VALUES (%s, %s, %s)",
            (rep.watermark, rep.ok, json.dumps(rep.checks)),
        )
        pg.commit()
        return rep
    finally:
        pg.close()
