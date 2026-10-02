FROM apache/airflow:2.10.5-python3.12

# dbt lives in its own virtualenv: its pinned dependencies conflict with Airflow's.
USER root
RUN python -m venv /opt/dbt && /opt/dbt/bin/pip install --no-cache-dir \
        "dbt-core>=1.9,<1.10" "dbt-postgres>=1.9,<1.10" \
    && ln -s /opt/dbt/bin/dbt /usr/local/bin/dbt
USER airflow

# The pipeline package, installed into Airflow's own environment (pandas is already there).
COPY --chown=airflow pyproject.toml README.md /opt/olistwh/
COPY --chown=airflow src /opt/olistwh/src
RUN pip install --no-cache-dir "pymysql>=1.1" "psycopg[binary]>=3.2" \
    && pip install --no-cache-dir --no-deps /opt/olistwh

WORKDIR /opt/olistwh
