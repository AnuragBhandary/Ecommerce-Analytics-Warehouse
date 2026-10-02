"""Shared fixtures. Integration tests use their own databases (olist_test in MySQL,
warehouse_test in PostgreSQL) so they never touch the demo data."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import psycopg
import pymysql
import pytest

from olistwh import simulator
from olistwh.config import Settings
from olistwh.timeline import Timeline

SAMPLE = Path(__file__).parent / "fixtures" / "olist_sample"
BASE = Settings.from_env()
TEST_DSN = BASE.pg_dsn.rsplit("/", 1)[0] + "/warehouse_test"


@pytest.fixture(scope="session")
def timeline() -> Timeline:
    return Timeline.load(SAMPLE)


@pytest.fixture(scope="session")
def _databases() -> Settings:
    try:
        root = pymysql.connect(
            host=BASE.mysql_host,
            port=BASE.mysql_port,
            user="root",
            password=os.environ.get("MYSQL_ROOT_PASSWORD", "root"),
        )
        admin = psycopg.connect(BASE.pg_dsn, autocommit=True)
    except Exception as e:  # pragma: no cover - only when the containers are down
        pytest.skip(f"databases not reachable ({e}); run `make infra`")
    with root.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS olist_test")
        cur.execute(f"GRANT ALL ON olist_test.* TO '{BASE.mysql_user}'@'%'")
    root.close()
    if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'warehouse_test'").fetchone():
        admin.execute("CREATE DATABASE warehouse_test")
    admin.close()
    return replace(BASE, mysql_db="olist_test", pg_dsn=TEST_DSN, data_dir=SAMPLE)


@pytest.fixture
def settings(_databases: Settings) -> Iterator[Settings]:
    """Fresh, empty source and warehouse for every test."""
    my = _databases.mysql()
    simulator.reset(my)
    my.close()
    with psycopg.connect(_databases.pg_dsn, autocommit=True) as pg:
        for schema in ("raw", "staging", "snapshots", "marts"):
            pg.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")  # type: ignore[arg-type]
    yield _databases


@pytest.fixture
def env(settings: Settings) -> dict[str, str]:
    """Environment for running the CLI or dbt as a subprocess against the test databases."""
    return {
        **os.environ,
        "MYSQL_DATABASE": settings.mysql_db,
        "WAREHOUSE_DSN": settings.pg_dsn,
        "OLIST_DATA_DIR": str(settings.data_dir),
        "DBT_DBNAME": "warehouse_test",
    }
