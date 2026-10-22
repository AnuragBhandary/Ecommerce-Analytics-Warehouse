"""Generate the Olist Power BI project (PBIP: TMDL model + PBIR report) for Ecommerce-Analytics-Warehouse."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from pbip_lib import (agg, col, csv_partition_m, gradient, lit, meas, proj, range_filter, short, table_tmdl,
                      topn_filter, visual, write_pbip, write_report, write_semantic_model)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # tools/powerbi -> repo root
# Output folder: argv[1], else tools/powerbi/out/olist-report. Pass powerbi/olist-report only to
# replace the committed (Desktop-saved) project on purpose.
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out", "olist-report")
NAME = "olist-report"
EXPORTS = os.path.join(REPO, "exports")

H = {"hidden": True, "sum": "none", "fmt": "0"}  # surrogate / date keys
N = {"sum": "none"}

TABLES = {
    "dim_date": [
        ("date_key", "int", H), ("date", "date", {"key": True}), ("year", "int", {"sum": "none", "fmt": "0"}),
        ("quarter", "int", N), ("month", "int", N), ("month_name", "text", {"sortBy": "month"}),
        ("year_month", "text"), ("month_start", "date"), ("day_of_week", "int", N),
        ("day_name", "text", {"sortBy": "day_of_week"}), ("is_weekend", "bool"),
    ],
    "dim_customer": [
        ("customer_sk", "text", {"hidden": True}), ("customer_unique_id", "text"), ("zip_code_prefix", "text"),
        ("city", "text"), ("state", "text"), ("address_effective_at", "dt"), ("valid_from", "dt"),
        ("valid_to", "dt"), ("is_current", "bool"), ("version_count", "int", N),
    ],
    "dim_product": [
        ("product_sk", "text", {"hidden": True}), ("product_id", "text"), ("category", "text"),
        ("category_pt", "text"), ("photos_qty", "int", N), ("weight_g", "num", N), ("volume_cm3", "num", N),
    ],
    "dim_seller": [
        ("seller_sk", "text", {"hidden": True}), ("seller_id", "text"), ("zip_code_prefix", "text"),
        ("city", "text"), ("state", "text"),
    ],
    "fct_orders": [
        ("order_id", "text"), ("customer_sk", "text", {"hidden": True}), ("customer_unique_id", "text"),
        ("purchase_date_key", "int", H), ("order_status", "text"), ("purchased_at", "dt"), ("approved_at", "dt"),
        ("delivered_carrier_at", "dt"), ("delivered_customer_at", "dt"), ("estimated_delivery_date", "date"),
        ("item_count", "int"), ("seller_count", "int"), ("items_value", "cur"), ("freight_value", "cur"),
        ("gross_revenue", "cur"), ("payment_value", "cur"), ("is_delivered", "bool"), ("delivery_days", "num"),
        ("delay_days", "num"), ("is_late", "bool"), ("customer_order_number", "int", N),
        ("is_first_order", "bool"), ("cohort_month", "date"),
    ],
    "fct_order_items": [
        ("order_id", "text"), ("order_item_id", "int", N), ("product_sk", "text", {"hidden": True}),
        ("seller_sk", "text", {"hidden": True}), ("purchase_date_key", "int", H), ("order_status", "text"),
        ("shipping_limit_at", "dt"), ("price", "cur"), ("freight_value", "cur"), ("item_revenue", "cur"),
    ],
    "fct_payments": [
        ("order_id", "text", {"hidden": True}), ("payment_sequential", "int", N), ("purchase_date_key", "int", H),
        ("payment_type", "text"), ("installments", "int", N), ("payment_value", "cur"),
    ],
    "fct_reviews": [
        ("review_id", "text"), ("order_id", "text", {"hidden": True}), ("sent_date_key", "int", H),
        ("score", "int", N), ("is_answered", "bool"), ("has_comment", "bool"), ("sent_at", "dt"),
        ("answered_at", "dt"), ("response_hours", "num", N),
    ],
    "mart_seller_monthly": [
        ("seller_id", "text"), ("seller_state", "text"), ("seller_city", "text"), ("month", "date"),
        ("orders", "int"), ("items", "int"), ("revenue", "cur"), ("delivered_orders", "int"),
        ("late_orders", "int"), ("avg_review_score", "num", {"sum": "average"}),
    ],
    "mart_customer_cohorts": [
        ("cohort_month", "date"), ("months_since_first", "int", N), ("active_customers", "int"),
        ("cohort_size", "int"), ("retention_rate", "num", {"sum": "none", "fmt": "0.0%"}),
    ],
}

DESCRIPTIONS = {
    "dim_date": "Calendar 2016-2019, marked as the date table (time intelligence needs it contiguous).",
    "dim_customer": "SCD2 customer dimension: one row per address version.",
    "fct_orders": "One row per order. Revenue, delivery and cohort flags.",
    "fct_order_items": "One row per order line. Not related to fct_orders (see relationships.tmdl).",
}

RELATIONSHIPS = [
    ("fct_orders.purchase_date_key", "dim_date.date_key", {}),
    ("fct_orders.customer_sk", "dim_customer.customer_sk", {}),
    ("fct_order_items.purchase_date_key", "dim_date.date_key", {}),
    ("fct_order_items.product_sk", "dim_product.product_sk", {}),
    ("fct_order_items.seller_sk", "dim_seller.seller_sk", {}),
    ("fct_reviews.order_id", "fct_orders.order_id", {}),
    ("fct_reviews.sent_date_key", "dim_date.date_key", {"inactive": True}),
    ("fct_payments.order_id", "fct_orders.order_id", {}),
    ("mart_seller_monthly.month", "dim_date.date", {}),
    ("mart_customer_cohorts.cohort_month", "dim_date.date", {}),
]

ROLES = [
    (f"Seller region {s}", [("dim_seller", f'[state] = "{s}"'), ("mart_seller_monthly", f'[seller_state] = "{s}"')])
    for s in ("SP", "RJ", "MG")
]

BRL = "\\R\\$ #,0"
FORMATS = {
    "Average Order Value": "\\R\\$ #,0.00",
    "Avg Delivery Days": "0.0", "Avg Days Late (late orders)": "0.0",
    "Avg Review Score": "0.00", "Avg Review Score (late)": "0.00", "Avg Review Score (on time)": "0.00",
    "Avg Installments (credit card)": "0.0", "Seller Revenue Rank": "#,0", "Last Order Date": "yyyy-mm-dd", "Analysis Start": "yyyy-mm-dd",
}
BRL_MEASURES = {"Revenue", "Item Revenue", "Revenue PY", "Revenue PM", "Revenue YTD", "Revenue Running Total",
                "Revenue 3M Moving Avg", "Payment Value", "Seller Revenue"}


def parse_measures(path):
    """Read powerbi/measures.dax: `Name =` at column 0, body until a blank line; // lines above = description."""
    lines = open(path, encoding="utf-8").read().split("\n")
    out, desc, folder, i = [], [], "Core", 0
    while i < len(lines):
        line = lines[i]
        sec = re.match(r"^// -{10,} (.+)$", line)
        if sec:
            folder, desc = sec.group(1).strip().capitalize(), []
        elif line.startswith("// ="):
            desc = []
        elif line.startswith("//"):
            desc.append(line[2:].strip())
        elif re.match(r"^[A-Za-z].* =\s*$", line):
            name = line.rsplit("=", 1)[0].strip()
            body = []
            i += 1
            while i < len(lines) and lines[i].strip():
                body.append(lines[i])
                i += 1
            if name in FORMATS:
                fmt = FORMATS[name]
            elif name in BRL_MEASURES:
                fmt = BRL
            elif name.endswith("%") or name == "Review Response Rate":
                fmt = "0.0%"
            elif name == "Selected Seller":
                fmt = None
            else:
                fmt = "#,0"
            out.append({"name": name, "expr": "\n".join(body), "fmt": fmt, "folder": folder,
                        "desc": " ".join(desc) or None})
            desc = []
            continue
        elif not line.strip():
            pass
        i += 1
    return out


def build_model():
    tables = []
    for t, cols in TABLES.items():
        m = csv_partition_m(f"{t}.csv", cols, "ExportsFolder")
        tables.append((t, table_tmdl(t, cols, m, data_category="Time" if t == "dim_date" else None,
                                     description=DESCRIPTIONS.get(t))))
    measures = parse_measures(os.path.join(REPO, "powerbi", "measures.dax"))
    mt = table_tmdl("_Measures", [("Dummy", "text", {"hidden": True})], "#table(type table [Dummy = text], {})",
                    description="Measure table: every DAX measure lives here (see powerbi/measures.dax).",
                    measures=measures)
    tables.insert(0, ("_Measures", mt))
    expressions = [
        "/// Folder holding the `make export` CSVs. Change it in Transform data > Manage parameters.",
        f'expression ExportsFolder = "{EXPORTS}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]',
        "\tannotation PBI_ResultType = Text",
        "",
    ]
    order = ["ExportsFolder", "_Measures"] + list(TABLES)
    write_semantic_model(ROOT, NAME, tables, RELATIONSHIPS, ROLES, expressions, order)
    return measures


M = "_Measures"


def cards(page, names, x0, y, w, h, gap=10):
    return [visual(f"{page}Card{i}", "card", (x0 + i * (w + gap), y, w, h), {"Values": [meas(n)]})
            for i, n in enumerate(names)]


def slicer(name, pos, entity, column, title):
    return visual(name, "slicer", pos, {"Values": [col(entity, column)]}, title=title,
                  objects={"data": [{"properties": {"mode": lit("Dropdown")}}],
                           "header": [{"properties": {"show": lit(False)}}]})


def window(name):
    """Jan 2017 - Aug 2018: complete months after Olist's 2016 launch period."""
    return range_filter(name, "dim_date", "month_start", [(">=", "datetime'2017-01-01T00:00:00'"),
                                                            ("<=", "datetime'2018-08-01T00:00:00'")])


def build_report():
    month = col("dim_date", "month_start")

    p1 = [
        slicer("ovYear", (20, 10, 200, 70), "dim_date", "year", "Year"),
        slicer("ovState", (230, 10, 200, 70), "dim_customer", "state", "Customer state"),
        *cards("ov", ["Revenue", "Orders", "Average Order Value", "Customers", "On-time Delivery %",
                      "Avg Review Score"], 20, 90, 198, 100),
        visual("ovRevenueTrend", "lineChart", (20, 205, 760, 500),
               {"Category": [month], "Y": [meas("Revenue"), meas("Revenue PY")]},
               title="Revenue by month vs prior year (Jan 2017 - Aug 2018)",
               sort=[(month, "Ascending")], filters=[window("ovWindow")]),
        visual("ovTopCategories", "clusteredBarChart", (790, 205, 470, 500),
               {"Category": [col("dim_product", "category")], "Y": [meas("Item Revenue")]},
               title="Top 10 categories by revenue",
               sort=[(meas("Item Revenue"), "Descending")],
               filters=[topn_filter("ovTop10", "dim_product", "category", "Item Revenue", 10)]),
    ]

    ym = col("dim_date", "year_month")
    yoy = meas("Revenue YoY %")
    p2 = [
        visual("stCombo", "lineClusteredColumnComboChart", (20, 20, 760, 330),
               {"Category": [month], "Y": [meas("Revenue")], "Y2": [meas("Revenue MoM %")]},
               title="Monthly revenue and MoM growth", sort=[(month, "Ascending")]),
        visual("stRunning", "lineChart", (20, 360, 375, 345),
               {"Category": [month], "Y": [meas("Revenue Running Total")]},
               title="Revenue running total", sort=[(month, "Ascending")]),
        visual("stMoving", "lineChart", (405, 360, 375, 345),
               {"Category": [month], "Y": [meas("Revenue"), meas("Revenue 3M Moving Avg")]},
               title="Revenue vs 3-month moving average", sort=[(month, "Ascending")]),
        visual("stMatrix", "pivotTable", (790, 20, 470, 440),
               {"Rows": [ym], "Values": [meas("Revenue"), meas("Revenue PY"), yoy, meas("Revenue YTD")]},
               title="Revenue by month: prior year, YoY, YTD",
               sort=[(ym, "Ascending")],
               objects={"values": [gradient(yoy, f"{M}.Revenue YoY %", ["#FFFFFF", "#63BE7B"])]}),
        visual("stPayments", "donutChart", (790, 470, 300, 235),
               {"Category": [col("fct_payments", "payment_type")], "Y": [meas("Payment Value")]},
               title="Payment value by type"),
        visual("stVoucher", "card", (1100, 470, 160, 235), {"Values": [meas("Voucher Share %")]}),
    ]

    seller_id = col("mart_seller_monthly", "seller_id")
    ret = meas("Cohort Retention %")
    p3 = [
        *cards("dl", ["On-time Delivery %", "Avg Delivery Days", "Avg Days Late (late orders)"], 20, 20, 200, 110),
        visual("dlOnTimeTrend", "lineChart", (20, 140, 620, 220),
               {"Category": [month], "Y": [meas("On-time Delivery %")]},
               title="On-time delivery % by month", sort=[(month, "Ascending")]),
        visual("dlReviewGap", "clusteredBarChart", (650, 20, 610, 170),
               {"Y": [meas("Avg Review Score (on time)"), meas("Avg Review Score (late)")]},
               title="Average review score: on time vs late",
               objects={"labels": [{"properties": {"show": lit(True)}}]}),
        visual("dlByState", "clusteredColumnChart", (650, 200, 610, 160),
               {"Category": [col("dim_customer", "state")], "Y": [meas("On-time Delivery %")]},
               title="On-time delivery % by customer state",
               sort=[(meas("On-time Delivery %"), "Ascending")]),
        visual("dlCohorts", "pivotTable", (20, 370, 760, 335),
               {"Rows": [col("mart_customer_cohorts", "cohort_month")],
                "Columns": [col("mart_customer_cohorts", "months_since_first")], "Values": [ret]},
               title="Cohort retention: share of each monthly cohort ordering again, by months since first order",
               filters=[range_filter("dlMonthsAfter", "mart_customer_cohorts", "months_since_first", [(">", "0L")])],
               objects={"values": [gradient(ret, f"{M}.Cohort Retention %", ["#FFFFFF", "#1F4E79"])]}),
        visual("dlSellers", "tableEx", (790, 370, 470, 335),
               {"Values": [seller_id, col("mart_seller_monthly", "seller_state"), meas("Seller Revenue"),
                           meas("Seller Revenue Rank"), meas("Seller Late Rate %")]},
               title="Sellers (right-click a seller > Drill through > Seller detail)",
               sort=[(meas("Seller Revenue"), "Descending")]),
    ]

    sm = "mart_seller_monthly"
    smonth = col(sm, "month")
    p4 = [
        visual("sdBack", "actionButton", (20, 20, 40, 40),
               objects={"icon": [{"properties": {"shapeType": lit("back")}, "selector": {"id": "default"}}]},
               vco={"visualLink": [{"properties": {"show": lit(True), "type": lit("Back")}}]}),
        visual("sdHeader", "card", (70, 10, 1190, 70), {"Values": [meas("Selected Seller")]},
               objects={"categoryLabels": [{"properties": {"show": lit(False)}}]}),
        *cards("sd", ["Seller Revenue", "Seller Revenue Rank", "Seller Late Rate %"], 20, 90, 300, 100),
        visual("sdCardReview", "card", (950, 90, 310, 100), {"Values": [proj(agg(sm, "avg_review_score", 1),
                                                                             "Avg review score")]}),
        visual("sdTrend", "lineChart", (20, 205, 620, 500), {"Category": [smonth], "Y": [meas("Seller Revenue")]},
               title="Seller revenue by month", sort=[(smonth, "Ascending")]),
        visual("sdTable", "tableEx", (650, 205, 610, 500),
               {"Values": [smonth, proj(agg(sm, "orders"), "Orders"), proj(agg(sm, "revenue"), "Revenue"),
                           proj(agg(sm, "late_orders"), "Late orders"),
                           proj(agg(sm, "avg_review_score", 1), "Avg review score")]},
               title="Monthly detail", sort=[(smonth, "Ascending")]),
    ]

    pages = [
        {"name": "overview", "displayName": "Overview", "visuals": p1},
        {"name": "salestrends", "displayName": "Sales trends", "visuals": p2, "filters": [window("stWindow")]},
        {"name": "delivery", "displayName": "Delivery and reviews", "visuals": p3, "filters": [window("dlWindow")]},
        {"name": "sellerdetail", "displayName": "Seller detail", "visuals": p4,
         "filters": [{"name": "sdDrillSeller", "field": seller_id, "type": "Categorical",
                      "howCreated": "Drillthrough"}],
         "binding": {"name": short("seller-drill"), "type": "Drillthrough",
                     "parameters": [{"name": short("seller-param"), "boundFilter": "sdDrillSeller",
                                     "fieldExpr": seller_id}]}},
    ]
    write_report(ROOT, NAME, pages)


if __name__ == "__main__":
    ms = build_model()
    build_report()
    write_pbip(ROOT, NAME)
    print(f"{len(ms)} measures:", ", ".join(m["name"] for m in ms))
