# Interview guide

How to learn this codebase, pitch it, and defend every decision in a data-engineering or
analytics deep dive. Numbers are in [RESULTS.md](RESULTS.md); reasoning is in
[DESIGN.md](DESIGN.md).

## 1. Learning path (read in this order)

| # | File | What to be able to explain afterwards |
|---|---|---|
| 1 | `src/olistwh/tables.py` | Why one table spec generates both DDLs; why `updated_at` is `ON UPDATE CURRENT_TIMESTAMP(6)`; why reviews have a composite key |
| 2 | `src/olistwh/timeline.py` | How a static CSV becomes lifecycles; `changed_between`; why stage times are forced monotonic |
| 3 | `src/olistwh/simulator.py` | One tick = one transaction including the clock; why upserts don't bump unchanged rows |
| 4 | `src/olistwh/extract_load.py` | Consistent snapshot, cutoff, lookback, merge guard, watermark in the same transaction |
| 5 | `src/olistwh/reconcile.py` | missing / stale / phantom; why "settled before the watermark" |
| 6 | `warehouse/snapshots/snap_customers.sql` + `dim_customer.sql` | SCD2 on business time; the 1900 first-version rule |
| 7 | `warehouse/models/marts/fct_orders.sql` | Incremental with three change sources; point-in-time customer join |
| 8 | `warehouse/tests/*.sql`, `_marts.yml` | What each test protects against |
| 9 | `airflow/dags/olist_elt.py` | Task order, retries, why max_active_runs=1 |
| 10 | `tests/test_pipeline.py` | Late commit, SIGKILL, tampering: what each assertion proves |
| 11 | `powerbi/measures.dax`, `excel/README.md` | Each DAX measure out loud; filter vs row context |

Exercise after each file: close it and rewrite the key query or function on paper.

## 2. The 60-second pitch

> "I built an analytics warehouse for a Brazilian e-commerce marketplace, about a hundred thousand
> orders. The dataset is a static Kaggle snapshot, so the first thing I built was a simulator that
> replays its timeline into MySQL: orders arrive, then get approved, shipped and delivered,
> reviews get answered, customers move. That made the hard parts real. The extract is incremental
> on updated_at with a lookback window for late commits, and it commits data and watermark in one
> transaction, so a crash mid-load leaves nothing behind and reruns are idempotent. In dbt I
> modeled a star schema with incremental MERGE facts and an SCD Type 2 customer dimension, so
> every order joins to the address the customer had when they bought. Airflow runs extract, dbt
> snapshot, run, test, and a reconciliation that checks the warehouse against the source row by
> row and fails the DAG on any mismatch. On top, there are Metabase dashboards, a Power BI report
> and an Excel workbook, all reading the same tested marts. To prove it, I replayed all 26 months
> in ticks, killed a load mid-transaction, and showed the incremental result is identical to a
> full rebuild."

## 3. Numbers to know cold

See [RESULTS.md](RESULTS.md). At minimum: ~100k orders and how many rows land in raw; 26 ticks,
every one passing 85 dbt tests and every reconciliation check; the SIGKILL result; incremental
equals full refresh (hash-identical); incremental vs full-refresh dbt time; 259 address changes in
the source and how many became SCD2 versions.

## 4. Likely questions

**"Why not just reload everything each time?"** At 100k orders it would work. But the cost grows
with history, and it hides change semantics. I still use a full rebuild as the *oracle*: the
proof run checks incremental output against `--full-refresh`, byte for byte.

**"What's a late-arriving commit and how do you handle it?"** `updated_at` is set when the
statement executes, not when the transaction commits. A transaction that starts before my cutoff
and commits after it has rows behind my watermark. A pure `updated_at > watermark` misses them
forever. I re-read a 10-minute lookback window each run, and the merge only applies strictly newer
versions, so the re-read is free. I have a test that holds a transaction open to prove both the
bug and the fix.

**"How do you make the load idempotent?"** Three pieces: a deterministic read window, a merge
keyed on the primary key that ignores older-or-equal versions, and committing the watermark in the
same transaction as the data. Rerun with no new data → 0 rows applied, same hashes.

**"What if the job dies halfway?"** Then nothing happened. The data and the watermark are in one
PostgreSQL transaction, and a SIGKILL rolls both back. The test kills it after two tables are
merged but before commit, checks that raw and the watermark are unchanged, retries, and reconciles.

**"Why a consistent snapshot on the MySQL side?"** Without it, orders are read at time t1 and
items at t2, so raw can hold items whose order isn't there yet. That breaks the referential tests
and the revenue totals.

**"Explain your SCD2."** dbt snapshot with the timestamp strategy on `address_updated_at`, which
is business time, not load time. So validity windows line up with order timestamps and the
point-in-time join is correct. First version starts at 1900 so no order is orphaned. Tests: no
overlaps or gaps, one current row per customer, no null customer keys. Limit: two moves between
runs collapse into one; CDC would fix that.

**"Incremental models: how do you know they're right?"** Hash every mart after the incremental
path, run `--full-refresh`, hash again: identical. The incremental filter uses `_loaded_at` from
the loader, not source timestamps, because source timestamps can arrive late. `fct_orders` unions
three change sources, since an order's revenue depends on its items and payments too.

**"What does reconciliation catch that dbt tests don't?"** dbt tests check the warehouse against
itself. Reconciliation checks it against the *source*: a missing row, a stale version, a
deleted-at-source row, or a revenue difference of one centavo. The test suite tampers with raw and
asserts exactly those checks fire.

**"Hard deletes?"** Not visible to an updated_at extractor. The source uses statuses instead, and
reconciliation would flag a deleted row as a phantom. The fix is soft deletes, a periodic
key-set diff, or CDC (my second project does binlog CDC).

**"Why Postgres as the warehouse, not Snowflake or BigQuery?"** Free, local, and the modeling and
dbt code is the same. The design (raw, staging, marts, incremental merge, snapshots) carries over
directly. I'd change the materializations' cluster and partition settings, not the logic.

**Power BI: "difference between a calculated column and a measure?"** A column is computed per
row at refresh and stored. A measure is computed at query time in the current filter context.
Revenue is a measure, because it has to respond to slicers.

**"Explain `CALCULATE`."** It evaluates an expression in a modified filter context. Its filter
arguments replace or add filters, and inside an iterator it turns the current row into a filter
(context transition). `Repeat Customer %` relies on exactly that.

**"Why did you exclude canceled orders from Revenue, and is that the same everywhere?"** Canceled
or unavailable orders never generated revenue. The rule is in the dbt mart for Excel, in the DAX
measure, and in the Metabase SQL, and Excel has a reconciliation cell against the Power BI total.

## 5. Behavioral hooks

- **A bug the pipeline caught:** the first Airflow run failed at `dbt_test` on the cohort test.
  Customers whose first order was canceled were assigned to a cohort with no successful order.
  Bad numbers never reached a dashboard; the fix was a one-line definition change, and the test
  stayed.
- **A bug only a comparison could find:** `fct_order_items` carried the order's status, but its
  incremental filter only looked at the item's load time. Items never change, so statuses froze
  at `created`. Every dbt test passed. The proof run hashed the incremental marts against
  `--full-refresh`, and they differed. I fixed the filter (reprocess items when their order
  changes) and added a test that item status equals order status, so the class of bug is caught
  on every run, not only in the proof. Lesson: an incremental model needs an oracle.
- **A source-data anomaly:** 74 reviews are dated before their order. In a tick-by-tick replay
  that's a review whose order doesn't exist yet. The simulator now gates visibility, and a warn
  test keeps the anomaly visible instead of hiding it.
- **Trade-off you chose:** snapshots over CDC for SCD2: simpler and good enough at 10-minute
  intervals, with the limitation documented and measured.
