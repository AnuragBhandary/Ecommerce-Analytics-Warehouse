"""Incremental extract-load: MySQL source -> PostgreSQL `raw` schema.

How a run works
---------------
1. Take a PostgreSQL advisory lock, so two runs can never interleave.
2. Open one MySQL transaction WITH CONSISTENT SNAPSHOT. Every table is read from the same point
   in time, so raw never holds an order item whose order has not arrived yet.
3. cutoff = MySQL NOW(6) - safety lag. Each table is read for
       watermark - lookback < updated_at <= cutoff
   The lookback re-reads a window that has already been loaded. It exists for *late commits*:
   updated_at is stamped when a statement runs, not when its transaction commits, so a slow
   transaction can commit a row whose updated_at is already behind the watermark. Any such row
   committed within `lookback` of its updated_at is still picked up by the next run.
4. Rows are COPYed into a temp table, then merged into raw with
       INSERT ... ON CONFLICT (pk) DO UPDATE ... WHERE raw._src_updated_at < new._src_updated_at
   so re-reading the lookback window is a no-op and an older version can never overwrite a
   newer one. Reruns are therefore idempotent.
5. The watermark moves to `cutoff` in the *same* PostgreSQL transaction as the data. A crash at
   any point rolls back the data and the watermark together; the next run starts from the old
   watermark and produces exactly the same raw tables.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .config import Settings
from .tables import TABLES, Table, pg_ddl

log = logging.getLogger(__name__)

LOCK_KEY = 0x01157E1  # arbitrary, constant advisory-lock id for this pipeline
EPOCH = datetime(1970, 1, 1)
FETCH = 5000


@dataclass
class RunResult:
    run_id: uuid.UUID
    watermark_from: datetime | None
    watermark_to: datetime
    extracted: dict[str, int] = field(default_factory=dict)
    applied: dict[str, int] = field(default_factory=dict)


def ensure_raw_schema(pg: psycopg.Connection) -> None:
    pg.execute("CREATE SCHEMA IF NOT EXISTS raw")
    for t in TABLES:
        pg.execute(pg_ddl(t))
        pg.execute(f"CREATE INDEX IF NOT EXISTS ix_{t.name}_loaded ON raw.{t.name} (_loaded_at)")
    pg.execute(
        "CREATE TABLE IF NOT EXISTS raw._watermarks ("
        " table_name TEXT PRIMARY KEY, watermark TIMESTAMP NOT NULL,"
        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    pg.execute(
        "CREATE TABLE IF NOT EXISTS raw._el_runs ("
        " run_id UUID PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,"
        " watermark_from TIMESTAMP, watermark_to TIMESTAMP NOT NULL,"
        " extracted JSONB NOT NULL, applied JSONB NOT NULL)"
    )
    pg.commit()


def _pause_hook(table: str) -> None:
    """Test hook: OLISTWH_EL_PAUSE_AFTER=<table> stops mid-run (after that table is merged but
    before commit) so a test can SIGKILL the process at the worst possible moment."""
    if os.environ.get("OLISTWH_EL_PAUSE_AFTER") == table:
        print(f"PAUSED_AFTER {table}", flush=True)
        sys.stdout.flush()
        time.sleep(600)


def _load_table(
    my_cur: Any, pg: psycopg.Connection, t: Table, lower: datetime, cutoff: datetime, run_id: str
) -> tuple[int, int]:
    cols = [*t.col_names, "created_at", "updated_at"]
    raw_cols = [*t.col_names, "_src_created_at", "_src_updated_at"]
    stage = f"_stage_{t.name}"
    pg.execute(
        f"CREATE TEMP TABLE {stage} ON COMMIT DROP AS "
        f"SELECT {', '.join(raw_cols)} FROM raw.{t.name} WITH NO DATA"
    )
    my_cur.execute(
        f"SELECT {', '.join(cols)} FROM `{t.name}` WHERE updated_at > %s AND updated_at <= %s",
        (lower, cutoff),
    )
    extracted = 0
    with (
        pg.cursor() as cur,
        cur.copy(f"COPY {stage} ({', '.join(raw_cols)}) FROM STDIN") as copy,
    ):
        while rows := my_cur.fetchmany(FETCH):
            for row in rows:
                copy.write_row(row)
            extracted += len(rows)

    non_pk = [c for c in raw_cols if c not in t.pk]
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*non_pk, "_loaded_at", "_run_id"])
    cur = pg.execute(
        f"INSERT INTO raw.{t.name} ({', '.join(raw_cols)}, _loaded_at, _run_id) "
        f"SELECT {', '.join(raw_cols)}, now(), %s FROM {stage} "
        f"ON CONFLICT ({', '.join(t.pk)}) DO UPDATE SET {sets} "
        f"WHERE raw.{t.name}._src_updated_at < EXCLUDED._src_updated_at",
        (run_id,),
    )
    return extracted, cur.rowcount


def run(
    settings: Settings,
    lookback: timedelta = timedelta(minutes=10),
    safety_lag: timedelta = timedelta(seconds=2),
) -> RunResult:
    pg = settings.pg()
    my = settings.mysql(streaming=True)
    run_id = uuid.uuid4()
    try:
        ensure_raw_schema(pg)
        pg.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        started = pg.execute("SELECT now()").fetchone()[0]  # type: ignore[index]
        marks: dict[str, datetime] = dict(
            pg.execute("SELECT table_name, watermark FROM raw._watermarks").fetchall()
        )

        my_cur = my.cursor()
        my_cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        my_cur.execute(
            "SELECT NOW(6) - INTERVAL %s MICROSECOND", (int(safety_lag.total_seconds() * 1e6),)
        )
        cutoff: datetime = my_cur.fetchall()[0][0]  # drain: SSCursor results must be consumed

        result = RunResult(run_id, min(marks.values()) if marks else None, cutoff)
        for t in TABLES:
            wm = marks.get(t.name)
            lower = wm - lookback if wm else EPOCH
            if wm and cutoff <= wm:
                raise RuntimeError(f"cutoff {cutoff} is not after watermark {wm}: clock skew?")
            ex, ap = _load_table(my_cur, pg, t, lower, cutoff, str(run_id))
            result.extracted[t.name], result.applied[t.name] = ex, ap
            pg.execute(
                "INSERT INTO raw._watermarks (table_name, watermark) VALUES (%s, %s) "
                "ON CONFLICT (table_name) DO UPDATE SET watermark = EXCLUDED.watermark,"
                " updated_at = now()",
                (t.name, cutoff),
            )
            log.info("%-22s extracted %7d  applied %7d", t.name, ex, ap)
            _pause_hook(t.name)
        my.rollback()

        pg.execute(
            "INSERT INTO raw._el_runs VALUES (%s, %s, clock_timestamp(), %s, %s, %s, %s)",
            (
                run_id,
                started,
                result.watermark_from,
                cutoff,
                Jsonb(result.extracted),
                Jsonb(result.applied),
            ),
        )
        pg.commit()
        return result
    except BaseException:
        pg.rollback()
        raise
    finally:
        my.close()
        pg.close()
