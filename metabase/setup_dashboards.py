"""Creates the Metabase dashboards through its REST API, so they live in git rather than in
someone's clicks. Safe to rerun: earlier copies are archived and rebuilt.

    python metabase/setup_dashboards.py          (or: make dashboards)

Dashboards: Revenue, Cohort retention, Delivery performance. All cards are native SQL over the
`marts` schema, read through the read-only `bi_reader` role.
Standard library only, so it runs anywhere Python does.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

MB = os.environ.get("MB_URL", "http://localhost:3000")
EMAIL = os.environ.get("MB_ADMIN_EMAIL", "admin@example.com")
PASSWORD = os.environ.get("MB_ADMIN_PASSWORD", "olist-local-dev-1")  # local dev only
DB_NAME = "Olist Warehouse"
COLLECTION = "Olist Warehouse"

session: dict[str, str] = {}


def api(method: str, path: str, body: Any = None) -> Any:
    req = urllib.request.Request(
        MB + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **session},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")
    return json.loads(raw) if raw else None


def login() -> None:
    props = api("GET", "/api/session/properties")
    if not props.get("has-user-setup"):
        res = api(
            "POST",
            "/api/setup",
            {
                "token": props["setup-token"],
                "user": {
                    "email": EMAIL,
                    "password": PASSWORD,
                    "first_name": "Local",
                    "last_name": "Admin",
                    "site_name": "Olist Warehouse",
                },
                "prefs": {"site_name": "Olist Warehouse", "allow_tracking": False},
            },
        )
        session["X-Metabase-Session"] = res["id"]
        print("Metabase initialised with the local admin account")
    else:
        res = api("POST", "/api/session", {"username": EMAIL, "password": PASSWORD})
        session["X-Metabase-Session"] = res["id"]


def database_id() -> int:
    for db in api("GET", "/api/database")["data"]:
        if db["name"] == DB_NAME:
            return int(db["id"])
    db = api(
        "POST",
        "/api/database",
        {
            "engine": "postgres",
            "name": DB_NAME,
            "is_full_sync": True,
            "details": {
                "host": os.environ.get("MB_WAREHOUSE_HOST", "postgres"),
                "port": 5432,
                "dbname": "warehouse",
                "user": "bi_reader",
                "password": "bi_reader",
                "schema-filters-type": "inclusion",
                "schema-filters-patterns": "marts",
            },
        },
    )
    return int(db["id"])


def collection_id() -> int:
    for c in api("GET", "/api/collection"):
        if c.get("name") == COLLECTION and not c.get("archived"):
            return int(c["id"])
    return int(api("POST", "/api/collection", {"name": COLLECTION, "color": "#509EE3"})["id"])


def clear(coll: int) -> None:
    for item in api("GET", f"/api/collection/{coll}/items")["data"]:
        kind = {"dashboard": "dashboard", "card": "card"}.get(item["model"])
        if kind:
            api("PUT", f"/api/{kind}/{item['id']}", {"archived": True})


def card(db: int, coll: int, name: str, sql: str, display: str, viz: dict[str, Any]) -> int:
    return int(
        api(
            "POST",
            "/api/card",
            {
                "name": name,
                "collection_id": coll,
                "display": display,
                "visualization_settings": viz,
                "dataset_query": {"type": "native", "database": db, "native": {"query": sql}},
            },
        )["id"]
    )


def dashboard(
    coll: int, name: str, description: str, layout: list[tuple[int, int, int, int, int]]
) -> int:
    d = api(
        "POST", "/api/dashboard", {"name": name, "collection_id": coll, "description": description}
    )
    api(
        "PUT",
        f"/api/dashboard/{d['id']}",
        {
            "dashcards": [
                {"id": -(i + 1), "card_id": cid, "row": r, "col": c, "size_x": w, "size_y": h}
                for i, (cid, r, c, w, h) in enumerate(layout)
            ]
        },
    )
    return int(d["id"])


VALID = "order_status not in ('canceled', 'unavailable')"
LINE = {"graph.dimensions": ["month"], "graph.metrics": ["revenue"]}


def main() -> None:
    for _ in range(60):
        try:
            if api("GET", "/api/health")["status"] == "ok":
                break
        except Exception:
            pass
        time.sleep(2)
    login()
    db, coll = database_id(), collection_id()
    api("POST", f"/api/database/{db}/sync_schema")
    clear(coll)

    def scalar(name: str, sql: str) -> int:
        return card(db, coll, name, sql, "scalar", {})

    k_rev = scalar("Revenue (R$)", f"select sum(gross_revenue) from marts.fct_orders where {VALID}")
    k_ord = scalar("Orders", f"select count(*) from marts.fct_orders where {VALID}")
    k_aov = scalar(
        "Average order value (R$)",
        f"select round(avg(gross_revenue), 2) from marts.fct_orders"
        f" where {VALID} and item_count > 0",
    )
    k_rep = scalar(
        "Repeat-customer share",
        f"select round(100.0 * count(distinct customer_unique_id)"
        " filter (where customer_order_number > 1)"
        f" / count(distinct customer_unique_id), 2) from marts.fct_orders where {VALID}",
    )
    rev_month = card(
        db,
        coll,
        "Revenue by month",
        f"""
        select d.month_start as month, sum(o.gross_revenue) as revenue, count(*) as orders
        from marts.fct_orders o join marts.dim_date d on d.date_key = o.purchase_date_key
        where {VALID} group by 1 order by 1""",
        "line",
        LINE,
    )
    rev_cat = card(
        db,
        coll,
        "Top 10 categories by revenue",
        f"""
        select p.category, sum(i.item_revenue) as revenue
        from marts.fct_order_items i join marts.dim_product p using (product_sk)
        where i.{VALID} group by 1 order by 2 desc limit 10""",
        "row",
        {"graph.dimensions": ["category"], "graph.metrics": ["revenue"]},
    )
    rev_state = card(
        db,
        coll,
        "Revenue by customer state",
        f"""
        select c.state, sum(o.gross_revenue) as revenue
        from marts.fct_orders o join marts.dim_customer c using (customer_sk)
        where {VALID} group by 1 order by 2 desc""",
        "bar",
        {"graph.dimensions": ["state"], "graph.metrics": ["revenue"]},
    )
    pay_mix = card(
        db,
        coll,
        "Payment mix",
        """
        select payment_type, sum(payment_value) as value from marts.fct_payments group by 1""",
        "pie",
        {"pie.dimension": "payment_type", "pie.metric": "value"},
    )

    cohort_tbl = card(
        db,
        coll,
        "Cohort retention (% of cohort ordering again in month N)",
        """
        select to_char(cohort_month, 'YYYY-MM') as cohort, max(cohort_size) as customers,
          """
        + ",\n          ".join(
            f"round(100 * max(retention_rate) filter (where months_since_first = {m}), 2) as m{m}"
            for m in range(1, 13)
        )
        + """
        from marts.mart_customer_cohorts group by cohort_month order by cohort_month""",
        "table",
        {},
    )
    cohort_curve = card(
        db,
        coll,
        "Average retention curve",
        """
        select months_since_first as month_n,
               round(100 * sum(active_customers)::numeric / sum(cohort_size), 3) as retention_pct
        from marts.mart_customer_cohorts where months_since_first between 1 and 12
        group by 1 order by 1""",
        "line",
        {"graph.dimensions": ["month_n"], "graph.metrics": ["retention_pct"]},
    )
    repeat = scalar(
        "Customers with 2+ orders",
        """
        select count(*) from (select customer_unique_id from marts.fct_orders
        group by 1 having count(*) > 1) x""",
    )

    late_month = card(
        db,
        coll,
        "Late-delivery rate by month (%)",
        """
        select d.month_start as month,
               round(100.0 * count(*) filter (where o.is_late) / count(*), 2) as late_pct,
               round(avg(o.delivery_days), 1) as avg_delivery_days
        from marts.fct_orders o join marts.dim_date d on d.date_key = o.purchase_date_key
        where o.is_delivered group by 1 order by 1""",
        "line",
        {"graph.dimensions": ["month"], "graph.metrics": ["late_pct", "avg_delivery_days"]},
    )
    delay_score = card(
        db,
        coll,
        "Review score by delivery delay",
        """
        select case when o.delay_days <= -10 then '1. 10+ days early'
                    when o.delay_days <= 0 then '2. on time'
                    when o.delay_days <= 7 then '3. 1-7 days late'
                    else '4. 8+ days late' end as delay_bucket,
               round(avg(r.score), 2) as avg_score, count(*) as reviews
        from marts.fct_orders o join marts.fct_reviews r using (order_id)
        where o.is_delivered and r.score is not null group by 1 order by 1""",
        "bar",
        {"graph.dimensions": ["delay_bucket"], "graph.metrics": ["avg_score"]},
    )
    k_late = scalar(
        "Late deliveries (%)",
        """
        select round(100.0 * count(*) filter (where is_late) / count(*), 2)
        from marts.fct_orders where is_delivered""",
    )
    k_days = scalar(
        "Median delivery time (days)",
        """
        select percentile_cont(0.5) within group (order by delivery_days)
        from marts.fct_orders where is_delivered""",
    )
    late_state = card(
        db,
        coll,
        "Late-delivery rate by customer state (%)",
        """
        select c.state, round(100.0 * count(*) filter (where o.is_late) / count(*), 2) as late_pct
        from marts.fct_orders o join marts.dim_customer c using (customer_sk)
        where o.is_delivered group by 1 having count(*) >= 100 order by 2 desc""",
        "bar",
        {"graph.dimensions": ["state"], "graph.metrics": ["late_pct"]},
    )

    ids = [
        dashboard(
            coll,
            "Revenue",
            "Revenue, orders and mix (canceled/unavailable excluded)",
            [
                (k_rev, 0, 0, 6, 3),
                (k_ord, 0, 6, 6, 3),
                (k_aov, 0, 12, 6, 3),
                (k_rep, 0, 18, 6, 3),
                (rev_month, 3, 0, 24, 6),
                (rev_cat, 9, 0, 12, 7),
                (rev_state, 9, 12, 12, 7),
                (pay_mix, 16, 0, 12, 6),
            ],
        ),
        dashboard(
            coll,
            "Cohort retention",
            "Monthly acquisition cohorts",
            [(repeat, 0, 0, 6, 3), (cohort_curve, 0, 6, 18, 6), (cohort_tbl, 6, 0, 24, 10)],
        ),
        dashboard(
            coll,
            "Delivery performance",
            "Delivery time vs promise, and what it does to reviews",
            [
                (k_late, 0, 0, 6, 3),
                (k_days, 0, 6, 6, 3),
                (late_month, 3, 0, 24, 6),
                (delay_score, 9, 0, 12, 7),
                (late_state, 9, 12, 12, 7),
            ],
        ),
    ]
    for i in ids:
        print(f"dashboard: {MB}/dashboard/{i}")


if __name__ == "__main__":
    main()
