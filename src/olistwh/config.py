"""Connection settings, read from the environment (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pymysql
from pymysql.cursors import SSCursor


@dataclass(frozen=True)
class Settings:
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_db: str
    pg_dsn: str
    data_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        return cls(
            mysql_host=e.get("MYSQL_HOST", "127.0.0.1"),
            mysql_port=int(e.get("MYSQL_PORT", "3307")),
            mysql_user=e.get("MYSQL_USER", "olist"),
            mysql_password=e.get("MYSQL_PASSWORD", "olist"),
            mysql_db=e.get("MYSQL_DATABASE", "olist"),
            pg_dsn=e.get(
                "WAREHOUSE_DSN", "postgresql://warehouse:warehouse@127.0.0.1:5433/warehouse"
            ),
            data_dir=Path(e.get("OLIST_DATA_DIR", "data")),
        )

    def mysql(self, streaming: bool = False) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.mysql_host,
            port=self.mysql_port,
            user=self.mysql_user,
            password=self.mysql_password,
            database=self.mysql_db,
            autocommit=False,
            charset="utf8mb4",
            cursorclass=SSCursor if streaming else pymysql.cursors.Cursor,
            # Every timestamp is UTC on both sides; nothing depends on the server's zone.
            init_command="SET time_zone = '+00:00'",
        )

    def pg(self) -> psycopg.Connection:
        conn = psycopg.connect(self.pg_dsn, autocommit=False)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.commit()
        return conn
