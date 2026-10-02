"""Replays the Olist timeline into MySQL, one tick at a time, the way an operational e-commerce
database would see it: new orders arrive, then move through approval, shipping and delivery;
review surveys go out and get answered; customers who order again from a new address get
their address updated.

The simulation clock (business time) lives in MySQL itself, in `sim_state`, and every tick is a
single transaction that writes the changed rows *and* moves the clock. A crash mid-tick leaves
both untouched, so ticks can simply be retried.

Rows are written with INSERT ... ON DUPLICATE KEY UPDATE. MySQL only bumps `updated_at` when a
value really changes, so the extractor sees each change exactly when it happens.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from typing import Any

import pandas as pd
import pymysql

from .config import Settings
from .tables import BY_NAME, TABLES, mysql_ddl
from .timeline import EPOCH, Timeline

log = logging.getLogger(__name__)

BATCH = 2000


def init_schema(conn: pymysql.connections.Connection) -> None:
    with conn.cursor() as cur:
        for t in TABLES:
            cur.execute(mysql_ddl(t))
        cur.execute(
            "CREATE TABLE IF NOT EXISTS sim_state ("
            " id TINYINT PRIMARY KEY, clock DATETIME NOT NULL,"
            " updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
            " ON UPDATE CURRENT_TIMESTAMP(6))"
        )
        cur.execute("INSERT IGNORE INTO sim_state (id, clock) VALUES (1, %s)", (EPOCH,))
    conn.commit()


def reset(conn: pymysql.connections.Connection) -> None:
    with conn.cursor() as cur:
        for t in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS `{t.name}`")
        cur.execute("DROP TABLE IF EXISTS sim_state")
    conn.commit()
    init_schema(conn)


def clock(conn: pymysql.connections.Connection, for_update: bool = False) -> pd.Timestamp:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT clock FROM sim_state WHERE id = 1" + (" FOR UPDATE" if for_update else "")
        )
        row = cur.fetchone()
    assert row is not None, "run `olistwh sim init` first"
    return pd.Timestamp(row[0])


def _clean(v: Any) -> Any:
    """pandas -> DB-API values: NaN/NaT become NULL, numpy scalars become Python ones."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item"):
        return v.item()
    return v


def _upsert(cur: pymysql.cursors.Cursor, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    t = BY_NAME[table]
    cols = t.col_names
    updates = [c for c in cols if c not in t.pk]
    sql = (
        f"INSERT INTO `{table}` ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
        # VALUES() is deprecated in favour of a row alias, but PyMySQL only rewrites
        # executemany into one multi-row INSERT when the statement has this shape.
        " ON DUPLICATE KEY UPDATE " + ", ".join(f"{c} = VALUES({c})" for c in updates or cols[:1])
    )
    rows = [tuple(_clean(v) for v in r) for r in df[cols].itertuples(index=False, name=None)]
    for i in range(0, len(rows), BATCH):
        cur.executemany(sql, rows[i : i + BATCH])
    return len(rows)


def advance(
    conn: pymysql.connections.Connection, tl: Timeline, until: pd.Timestamp
) -> dict[str, int]:
    """Moves the business clock forward to `until`, applying every change in between in one
    transaction. Returns the number of rows written per table."""
    now = clock(conn, for_update=True)  # also serialises concurrent simulators
    if until <= now:
        conn.rollback()
        return {}
    changes = tl.changed_between(now, until)
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        # Parents before children, as an application would write them.
        for t in TABLES:
            counts[t.name] = _upsert(cur, t.name, changes[t.name])
        cur.execute("UPDATE sim_state SET clock = %s WHERE id = 1", (until.to_pydatetime(),))
    conn.commit()
    return counts


def run(
    settings: Settings,
    until: datetime | None,
    step: pd.Timedelta,
    interval_s: float,
    stop_at_end: bool = True,
) -> None:
    """Advances in `step`-sized ticks, sleeping `interval_s` between them (0 = as fast as
    possible), until `until` or the end of the data."""
    tl = Timeline.load(settings.data_dir)
    target = pd.Timestamp(until) if until else tl.end
    conn = settings.mysql()
    try:
        init_schema(conn)
        while True:
            now = clock(conn)
            conn.commit()
            if now >= target:
                if stop_at_end:
                    log.info("clock at %s, target reached", now)
                    return
                time.sleep(interval_s or 1)
                continue
            nxt = min(target, max(now, tl.start - pd.Timedelta(seconds=1)) + step)
            counts = advance(conn, tl, nxt)
            log.info("clock %s -> %s: %s", now, nxt, {k: v for k, v in counts.items() if v})
            if interval_s:
                time.sleep(interval_s)
    finally:
        conn.close()
