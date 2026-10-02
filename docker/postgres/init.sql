-- Runs once, on an empty data volume. Separate databases keep Airflow's and Metabase's own
-- metadata out of the warehouse.
CREATE USER airflow WITH PASSWORD 'airflow';
CREATE DATABASE airflow OWNER airflow;
CREATE USER metabase WITH PASSWORD 'metabase';
CREATE DATABASE metabase OWNER metabase;

-- Read-only role for BI tools (Metabase, Power BI, Excel): marts only.
CREATE USER bi_reader WITH PASSWORD 'bi_reader';
\connect warehouse
CREATE SCHEMA IF NOT EXISTS marts AUTHORIZATION warehouse;
GRANT USAGE ON SCHEMA marts TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE warehouse IN SCHEMA marts GRANT SELECT ON TABLES TO bi_reader;
