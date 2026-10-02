{#
  SCD Type 2 history of each customer's (customer_unique_id) shipping address.

  Strategy `timestamp` on address_updated_at, which is *business* time: the purchase at which
  the address took effect. dbt_valid_from / dbt_valid_to therefore line up with order
  timestamps, so fct_orders can pick the address that was current when each order was placed.

  Limitation (documented in DESIGN.md): a snapshot sees the state at the moment it runs. If a
  customer moved twice between two runs, only the second move becomes a version.
#}
{% snapshot snap_customers %}
{{
    config(
        target_schema='snapshots',
        unique_key='customer_unique_id',
        strategy='timestamp',
        updated_at='address_updated_at',
    )
}}
select
    customer_unique_id,
    zip_code_prefix,
    initcap(city) as city,
    upper(state) as state,
    address_updated_at
from {{ source('raw', 'customers') }}
{% endsnapshot %}
