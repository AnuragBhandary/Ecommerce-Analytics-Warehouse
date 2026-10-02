"""One description of every source table, used to generate both the MySQL source DDL and the
PostgreSQL raw-layer DDL, so the two can never drift apart.

Each source table carries two bookkeeping columns maintained by MySQL itself:
  created_at  set once, on insert
  updated_at  DATETIME(6), ON UPDATE CURRENT_TIMESTAMP(6): it only changes when a value changes,
              which is what makes it a usable change-tracking column for incremental extraction.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Col:
    name: str
    mysql: str
    pg: str


@dataclass(frozen=True)
class Table:
    name: str
    pk: tuple[str, ...]
    cols: tuple[Col, ...]

    @property
    def col_names(self) -> list[str]:
        return [c.name for c in self.cols]


def _c(name: str, mysql: str, pg: str | None = None) -> Col:
    return Col(name, mysql, pg or mysql)


ID = "CHAR(32)"
TS = ("DATETIME", "TIMESTAMP")
MONEY = ("DECIMAL(12,2)", "NUMERIC(12,2)")

TABLES: tuple[Table, ...] = (
    Table(
        "category_translation",
        ("category_name",),
        (
            _c("category_name", "VARCHAR(64)", "TEXT"),
            _c("category_name_english", "VARCHAR(64)", "TEXT"),
        ),
    ),
    Table(
        "customers",
        ("customer_unique_id",),
        (
            _c("customer_unique_id", ID, "TEXT"),
            _c("zip_code_prefix", "CHAR(5)", "TEXT"),
            _c("city", "VARCHAR(64)", "TEXT"),
            _c("state", "CHAR(2)", "TEXT"),
            # Business time at which the current address took effect (the purchase that
            # introduced it). The dbt snapshot versions customers on this column.
            _c("address_updated_at", *TS),
        ),
    ),
    Table(
        "sellers",
        ("seller_id",),
        (
            _c("seller_id", ID, "TEXT"),
            _c("zip_code_prefix", "CHAR(5)", "TEXT"),
            _c("city", "VARCHAR(64)", "TEXT"),
            _c("state", "CHAR(2)", "TEXT"),
        ),
    ),
    Table(
        "products",
        ("product_id",),
        (
            _c("product_id", ID, "TEXT"),
            _c("category_name", "VARCHAR(64)", "TEXT"),
            _c("name_length", "INT", "INTEGER"),
            _c("description_length", "INT", "INTEGER"),
            _c("photos_qty", "INT", "INTEGER"),
            _c("weight_g", "INT", "INTEGER"),
            _c("length_cm", "INT", "INTEGER"),
            _c("height_cm", "INT", "INTEGER"),
            _c("width_cm", "INT", "INTEGER"),
        ),
    ),
    Table(
        "orders",
        ("order_id",),
        (
            _c("order_id", ID, "TEXT"),
            _c("customer_id", ID, "TEXT"),
            _c("customer_unique_id", ID, "TEXT"),
            _c("order_status", "VARCHAR(16)", "TEXT"),
            _c("purchased_at", *TS),
            _c("approved_at", *TS),
            _c("delivered_carrier_at", *TS),
            _c("delivered_customer_at", *TS),
            _c("estimated_delivery_date", "DATE"),
        ),
    ),
    Table(
        "order_items",
        ("order_id", "order_item_id"),
        (
            _c("order_id", ID, "TEXT"),
            _c("order_item_id", "INT", "INTEGER"),
            _c("product_id", ID, "TEXT"),
            _c("seller_id", ID, "TEXT"),
            _c("shipping_limit_at", *TS),
            _c("price", *MONEY),
            _c("freight_value", *MONEY),
        ),
    ),
    Table(
        "order_payments",
        ("order_id", "payment_sequential"),
        (
            _c("order_id", ID, "TEXT"),
            _c("payment_sequential", "INT", "INTEGER"),
            _c("payment_type", "VARCHAR(16)", "TEXT"),
            _c("installments", "INT", "INTEGER"),
            _c("payment_value", *MONEY),
        ),
    ),
    Table(
        "order_reviews",
        # review_id alone is not unique in Olist: 814 ids are reused across orders.
        ("review_id", "order_id"),
        (
            _c("review_id", ID, "TEXT"),
            _c("order_id", ID, "TEXT"),
            _c("score", "TINYINT", "SMALLINT"),
            _c("comment_title", "VARCHAR(64)", "TEXT"),
            _c("comment_message", "TEXT"),
            _c("sent_at", *TS),
            _c("answered_at", *TS),
        ),
    ),
)

BY_NAME = {t.name: t for t in TABLES}


def mysql_ddl(t: Table) -> str:
    cols = [f"  `{c.name}` {c.mysql}" + (" NOT NULL" if c.name in t.pk else "") for c in t.cols]
    cols += [
        "  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
        "  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
        " ON UPDATE CURRENT_TIMESTAMP(6)",
        f"  PRIMARY KEY ({', '.join(t.pk)})",
        # Covers the extraction query: WHERE updated_at > ? AND updated_at <= ? ORDER BY ...
        f"  KEY ix_{t.name}_updated (updated_at, {', '.join(t.pk)})",
    ]
    return (
        f"CREATE TABLE IF NOT EXISTS `{t.name}` (\n"
        + ",\n".join(cols)
        + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin"
    )


def pg_ddl(t: Table) -> str:
    cols = [f"  {c.name} {c.pg}" + (" NOT NULL" if c.name in t.pk else "") for c in t.cols]
    cols += [
        "  _src_created_at TIMESTAMP NOT NULL",
        "  _src_updated_at TIMESTAMP NOT NULL",
        "  _loaded_at TIMESTAMPTZ NOT NULL",
        "  _run_id UUID NOT NULL",
        f"  PRIMARY KEY ({', '.join(t.pk)})",
    ]
    return f"CREATE TABLE IF NOT EXISTS raw.{t.name} (\n" + ",\n".join(cols) + "\n)"
