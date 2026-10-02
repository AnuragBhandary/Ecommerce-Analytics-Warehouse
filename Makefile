.PHONY: install data infra up down seed el dbt reconcile pipeline dashboards export live test lint typecheck check prove

DBT = cd warehouse && DBT_PROFILES_DIR=. uv run dbt

install:            ## Python deps incl. dbt (needs uv)
	uv sync

data:               ## download the Olist dataset (~45 MB) into data/
	mkdir -p data && curl -sSL -o data/olist.zip \
		https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce \
		&& cd data && unzip -o -q olist.zip && rm olist.zip

infra:              ## MySQL + PostgreSQL only (enough for tests and the CLI)
	docker compose up -d --wait mysql postgres

up:                 ## everything: databases, Airflow (localhost:8080), Metabase (localhost:3000)
	docker compose up -d --build --wait

down:
	docker compose down

seed: infra         ## replay the source up to mid-2017 in one tick
	uv run olistwh sim advance --until 2017-06-01

el:
	uv run olistwh el

dbt:
	$(DBT) snapshot && $(DBT) build --exclude resource_type:snapshot

reconcile:
	uv run olistwh reconcile

pipeline: el dbt reconcile   ## the DAG's steps, by hand

dashboards:         ## create the Metabase dashboards through its API
	uv run python metabase/setup_dashboards.py

export:             ## marts -> exports/*.csv for Power BI / Excel without Docker
	uv run olistwh export-marts --out exports

live:               ## keep the source moving (6 business hours every 20 s)
	docker compose --profile live up -d simulator

test: infra
	uv run pytest --cov --cov-fail-under=90

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	uv run mypy

check: lint typecheck test

prove: infra        ## full-dataset proof run -> docs/results.json (pause the Airflow DAG first)
	uv run python scripts/prove.py
