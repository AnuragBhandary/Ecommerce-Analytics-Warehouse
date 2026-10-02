# Results

Produced by `make prove` ([scripts/prove.py](../scripts/prove.py)) on the full Olist dataset;
raw output in [results.json](results.json). Apple M5 Pro laptop, everything in Docker.

## The run

The whole dataset (Sep 2016 to Oct 2018) replayed in **27 ticks of 30 business days**. Each tick
runs the Airflow DAG's steps in order: simulator tick → extract-load → dbt snapshot → dbt run →
dbt test → reconcile.

| | |
|---|---|
| Orders / order items | **99,441 / 112,650** |
| Source row changes replayed (inserts + updates) | **597,649** |
| Rows in raw at the end | 547,343 (the ~50k difference: rows *updated* after insert, e.g. order status moves, answered reviews) |
| Revenue in marts (items + freight, all statuses) | R$ 15,843,553.24, identical to the source to the cent |
| Ticks passing all **85 dbt tests** | **27 / 27** (2 known-data-issue warnings) |
| Ticks passing all **32 reconciliation checks** | **27 / 27** |

## Correctness proofs

| Check | Result |
|---|---|
| Incremental marts vs `dbt run --full-refresh` (order-independent hash of every row of all 10 marts) | **identical** |
| Rerun EL + snapshot + dbt with no new source data | **identical hashes**; EL applies 0 rows |
| EL `SIGKILL`ed at tick 14, after merging `orders` and `order_items` but before commit | watermark and raw **unchanged**; the retry loaded the tick and reconciled 32/32 |
| SCD2: customer address changes in the source data | 259 |
| ... captured as SCD2 versions at 30-day ticks | **218 (84%)**, across 214 customers |

The 41 missing versions are the documented snapshot limit: a customer who moved twice within one
30-day tick only gets the second address versioned. The Airflow DAG runs every 10 minutes, where
that's far rarer. A change-log-based SCD2 (CDC) would capture all of them.

## Timings (per tick, median)

| Step | Time |
|---|---|
| Simulator tick (up to 45k row changes) | < 1 s |
| Extract-load | 2.3 s (max 6.0 s) |
| dbt snapshot | ~3.3 s |
| dbt run, incremental | 3.9 s |
| dbt run, full refresh (for comparison) | 5.3 s |
| dbt test (85 tests) | ~3.5 s |

At 100k orders a full refresh is only about 35% slower than incremental. The incremental design
earns its keep as history grows, and the proof shows it's *correct*, not just fast.

The EL time grows during the run because the proof compresses 26 months of business time into a
few minutes of wall-clock time. The 10-minute lookback window therefore re-reads almost every
row on every tick. They're all re-read, and none are re-applied (the MERGE guard skips equal
versions). In live operation the lookback holds only the last 10 minutes of changes.

## Bugs the checks caught while building this

Each of these would have shipped silently without the test that caught it.

1. **Cohort month 0 short of the full cohort** (caught by dbt test `assert_customer_cohort_starts_full`
   on the first Airflow run). Customers whose first order was canceled were put in a cohort with
   no successful order. Fix: define a cohort by the first successful order.
2. **Reviews dated before their order** (caught by the proof run's referential tests). 74 Olist
   surveys are dated up to 111 days before the purchase. The simulator now never makes a review
   visible before its order, and a warning test (`assert_reviews_not_sent_before_purchase`)
   keeps the anomaly visible.
3. **Stale order status on incremental items** (caught by the incremental-vs-full-refresh hash
   comparison). `fct_order_items` carries the order's status, but the incremental filter only
   looked at the *item's* load time, and items never change, so statuses froze at `created`.
   Fix: reprocess items when their order changes. A new test, `assert_item_status_matches_order`,
   now catches this kind of drift on every run.
4. **Every dashboard broke after a schema rebuild** (caught while taking these screenshots). The
   read-only BI role's `USAGE` grant on `marts` lived in the database init script, so dropping
   and rebuilding the schema silently removed it. Table grants survived, so nothing looked wrong
   from the warehouse side. Fix: dbt owns all BI grants (`grants` config plus an `on-run-end`
   schema grant), so they're re-applied on every run.
