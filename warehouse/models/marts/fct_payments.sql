{{
    config(
        materialized='incremental',
        unique_key=['order_id', 'payment_sequential'],
        incremental_strategy='merge',
        on_schema_change='fail',
    )
}}
select
    p.order_id,
    p.payment_sequential,
    cast(to_char(o.purchased_at, 'YYYYMMDD') as integer) as purchase_date_key,
    p.payment_type,
    p.installments,
    p.payment_value,
    p._loaded_at
from {{ ref('stg_payments') }} p
join {{ ref('stg_orders') }} o using (order_id)
{% if is_incremental() %}
where p._loaded_at >= (select coalesce(max(_loaded_at), '-infinity') from {{ this }})
{% endif %}
