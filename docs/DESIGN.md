# Design

## The problem with "load a Kaggle CSV into a warehouse"

The Olist dataset is a static snapshot: about 100k orders, each already in its final state.
Loading it once teaches nothing about the hard parts of data engineering, which are all about
**change**: rows that update after you loaded them, transactions that commit late, loads that
crash halfway, and dimensions whose attributes change over time.

So the source here is a **simulator** that replays the dataset's own timeline (Sep 2016 to
Oct 2018) into MySQL, the way an operational database would have seen it:

| Entity | What happens over business time |
|---|---|
| orders | inserted as `created` at purchase, then updated to `approved`, `shipped` and `delivered` at the dataset's own timestamps; the final status (canceled, unavailable, ...) applies at the order's last event |
| order_items, order_payments | inserted with their order |
| order_reviews | inserted when the survey is sent (score empty), updated when the customer answers |
| customers | one row per person (`customer_unique_id`); the address is **updated** when a later order ships to a new address (259 such changes in the data) |
| products, sellers | appear with their first sale |

`timeline.py` turns the CSVs into these lifecycles and can answer two questions:
`state_at(t)` and `changed_between(a, b)`. Each simulator tick upserts exactly the rows whose
state changed, in one MySQL transaction that also advances the clock stored in `sim_state`. A
unit test replays the data in random tick sizes and checks the result always equals one big
jump, which is the property everything downstream relies on.

MySQL's own `updated_at DATETIME(6) ON UPDATE CURRENT_TIMESTAMP(6)` only changes when a value
actually changes, so it's an honest change-tracking column.

## Architecture

```
MySQL 8.4 (source, simulator writes)            PostgreSQL 16 (warehouse)
  orders, order_items, ... ──olistwh el──▶  raw.*           exact copies + _src_updated_at, _loaded_at
                                              │ dbt snapshot
                                              ▼
                                            snapshots.snap_customers     SCD2, business-time validity
                                              │ dbt run
                                              ▼
                                            staging.* (views) ─▶ marts.* (star schema + marts)
                                              │                              │
                                     dbt test + reconcile          Metabase / Power BI / Excel
                                                                   (read-only role bi_reader)
```

Orchestration: Airflow DAG `olist_elt`: `extract_load → dbt_snapshot → dbt_run → dbt_test →
reconcile`, every 10 minutes, `max_active_runs=1`, retries with exponential backoff. Every task is
safe to retry.

## Extract-load: watermark, lookback, atomic commit

Alternatives considered:

| Approach | Why not (here) |
|---|---|
| Full reload every run | Simple and always correct, but the cost grows with history; it's the baseline the incremental path is tested against |
| CDC from the binlog (Debezium) | The right tool for low latency and hard deletes; it's Project 2. Batch analytics every 10 minutes doesn't need it |
| `updated_at > last_max_seen` | Misses **late commits**: `updated_at` is stamped when the statement runs, not when the transaction commits |

What `olistwh el` does:

1. A PostgreSQL **advisory lock**, so a manual run and a scheduled run can't interleave.
2. One MySQL transaction **WITH CONSISTENT SNAPSHOT**. All eight tables are read at the same
   instant, so raw never contains an item whose order hasn't been loaded.
3. `cutoff = NOW(6) − safety lag (2 s)`. Read `watermark − lookback < updated_at ≤ cutoff`.
   The **lookback (10 min)** re-reads recent history to catch transactions that committed late.
   The rule: the lookback must exceed the longest source transaction.
4. COPY into a temp table, then
   `INSERT … ON CONFLICT (pk) DO UPDATE … WHERE raw._src_updated_at < EXCLUDED._src_updated_at`.
   Re-reading the lookback window is a no-op (0 rows applied), and an older version can never
   overwrite a newer one.
5. Advance every table's watermark to `cutoff` **in the same PostgreSQL transaction** as the data,
   along with a row in `raw._el_runs`. A crash anywhere rolls back data and watermark together;
   the retry produces identical tables.

Tested: `test_lookback_catches_late_commits` holds a MySQL transaction open across an EL run,
shows that with `lookback=0` the row is lost forever (and that reconciliation flags it as
stale), and that the default lookback recovers it. `test_sigkill_mid_load_leaves_no_trace` kills
the process after two tables are merged but before commit.

**Not handled: hard deletes.** An updated_at extractor can't see a deleted row. The source here
never deletes (orders get a status instead). Reconciliation would report a deleted row as a
`phantom`. Real fixes: soft deletes in the source, a periodic PK-set diff, or CDC.

## Modeling

Kimball star schema, grain stated on every fact:

| Model | Grain | Materialization |
|---|---|---|
| `fct_orders` | one order | incremental MERGE on `order_id` |
| `fct_order_items` | one item in an order | incremental MERGE on `(order_id, order_item_id)` |
| `fct_payments` | one payment of an order | incremental MERGE |
| `fct_reviews` | one review survey of an order | incremental MERGE; updated in place when answered |
| `dim_customer` | one address version of a person | table, from the SCD2 snapshot |
| `dim_product`, `dim_seller` | one product / seller | table |
| `dim_date` | one day, 2016 to 2019 | table |
| `mart_seller_monthly` | seller × month | table, for Excel and the Power BI seller page |
| `mart_customer_cohorts` | cohort month × months since first order | table, for retention |

**Incremental facts.** Each model selects rows whose `_loaded_at` (set by the EL, monotonic per
run) is at or after the newest `_loaded_at` already in the model. `fct_orders` recomputes an order
if its order row *or* any of its items or payments changed; `fct_order_items` reprocesses an item
when its order changes, because it carries the order's status (the proof run caught the first
version, which didn't). The proof run checks the incremental
result is byte-identical to `dbt run --full-refresh`.

**Revenue definition.** `gross_revenue = Σ(price + freight)` per order: what the customer was
charged for goods and shipping. Payments total R$16.01M against R$15.84M of items and freight
over the full data; the difference is installment interest and vouchers. Dashboards exclude
`canceled` and `unavailable` orders; reconciliation compares *all* rows, because it checks data
movement, not business rules.

## SCD Type 2 on customers

Olist gives every order its own `customer_id`; `customer_unique_id` is the actual person. The
source keeps one row per person with their current address, and `address_updated_at` set to the
**business time** the address took effect.

`snap_customers` uses dbt's timestamp strategy **on that business-time column**, not on the load
time. That way `dbt_valid_from` is when the customer actually moved, and `fct_orders` joins each
order to the address version current at purchase:

```sql
on c.customer_unique_id = o.customer_unique_id
and o.purchased_at >= c.valid_from
and (c.valid_to is null or o.purchased_at < c.valid_to)
```

The first version of each customer gets `valid_from = 1900-01-01`, so every order has a match even
if the snapshot first saw the customer after an earlier order. Tests guarantee versions never
overlap or leave gaps, there's exactly one current version per customer, and every order has a
customer key.

**Known limit:** a snapshot sees the state when it runs. If a customer moved twice between two
runs, the middle address is never versioned. With 10-minute runs on live data that's rare; the
proof run reports how many of the source's address changes were captured at 30-day ticks. The
lossless alternative is to SCD2 from a change log (CDC) instead of snapshots.

## Data quality: three layers

1. **dbt tests (85):** keys unique and not null, foreign keys (`relationships`) from every fact
   to its dimensions, accepted values for statuses and payment types, numeric ranges, plus
   singular tests for business rules (SCD2 versions tile time, order revenue equals the sum of
   its items to the cent, unanswered surveys have no score, month 0 of each cohort is the whole
   cohort). One deliberate `warn`: 8 Olist orders are `delivered` with no delivery date. That's
   kept visible without blocking the pipeline.
2. **Reconciliation, source → raw, row level:** every source row created before the watermark is
   in raw; every row that settled before the watermark has the same version in raw; nothing in
   raw is missing from the source. Plus revenue and payment totals, exact to the cent.
3. **Reconciliation, raw → marts:** fact counts and revenue in the marts equal raw. It's the last
   DAG task, so a mismatch fails the run, and every result is stored in `raw._reconcile_runs`.

The cohort test earned its place on day one: the first Airflow run failed `dbt_test` because a
customer whose *first* order was canceled had a cohort month with no successful order in it. The
fix was to define a cohort by the first successful order. Bad numbers never reached a dashboard.

## Serving

- **Metabase** (in the stack): three dashboards (Revenue, Cohort retention, Delivery performance),
  created through the REST API by `metabase/setup_dashboards.py` so they're versioned and
  rebuildable.
- **Power BI:** a 4-page report with 33 DAX measures, drill-through and row-level security
  ([powerbi/README.md](../powerbi/README.md)).
- **Excel:** a seller-performance workbook (Power Query, PivotTables, XLOOKUP)
  ([excel/README.md](../excel/README.md)).

All three read the `marts` schema through `bi_reader`, a role that can only SELECT from marts.
Business rules live once, in dbt, so the three tools agree.
