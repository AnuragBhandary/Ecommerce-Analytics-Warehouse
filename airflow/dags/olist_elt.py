"""Olist warehouse pipeline.

    extract_load -> dbt_snapshot -> dbt_run -> dbt_test -> reconcile

- extract_load   incremental MySQL -> raw (watermark + lookback, atomic, idempotent)
- dbt_snapshot   SCD2 customer history, before the models that join to it
- dbt_run        staging views, dimensions, incremental MERGE facts, marts
- dbt_test       85 tests; any error fails the run before anyone reads bad numbers
- reconcile      source vs raw (row level) and raw vs marts (totals); exits 1 on mismatch

max_active_runs=1 because each step assumes the previous run finished; the extract also takes a
PostgreSQL advisory lock, so a manual run can't overlap a scheduled one either. Every task is
safe to retry: EL is idempotent, dbt merges on keys, reconcile only reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

DBT = (
    "cd /opt/olistwh/warehouse && "
    "DBT_TARGET_PATH=/tmp/dbt/target DBT_LOG_PATH=/tmp/dbt/logs "
    "dbt {cmd} --profiles-dir . --no-use-colors"
)

with DAG(
    dag_id="olist_elt",
    description="MySQL -> Postgres raw -> dbt star schema -> tests -> reconciliation",
    start_date=datetime(2026, 1, 1),
    schedule=timedelta(minutes=10),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "data-eng",
        "retries": 2,
        "retry_delay": timedelta(seconds=30),
        "retry_exponential_backoff": True,
    },
    tags=["olist", "warehouse"],
    doc_md=__doc__,
) as dag:
    extract_load = BashOperator(task_id="extract_load", bash_command="python -m olistwh el")
    dbt_snapshot = BashOperator(task_id="dbt_snapshot", bash_command=DBT.format(cmd="snapshot"))
    dbt_run = BashOperator(task_id="dbt_run", bash_command=DBT.format(cmd="run"))
    dbt_test = BashOperator(task_id="dbt_test", bash_command=DBT.format(cmd="test"))
    reconcile = BashOperator(task_id="reconcile", bash_command="python -m olistwh reconcile")

    extract_load >> dbt_snapshot >> dbt_run >> dbt_test >> reconcile
