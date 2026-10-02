{{
    config(
        materialized='incremental',
        unique_key='order_id',
        incremental_strategy='merge',
        on_schema_change='fail',
    )
}}
-- Grain: one row per order. Incremental: only orders whose order row, items or payments landed
-- since the last build are recomputed, then MERGEd on order_id.

with changed as (
    {% if is_incremental() %}
    {% set since = "(select coalesce(max(_loaded_at), '-infinity') from " ~ this ~ ")" %}
    select order_id from {{ ref('stg_orders') }} where _loaded_at >= {{ since }}
    union
    select order_id from {{ ref('stg_order_items') }} where _loaded_at >= {{ since }}
    union
    select order_id from {{ ref('stg_payments') }} where _loaded_at >= {{ since }}
    {% else %}
    select order_id from {{ ref('stg_orders') }}
    {% endif %}
),

orders as (
    select o.* from {{ ref('stg_orders') }} o join changed using (order_id)
),

items as (
    select
        order_id,
        count(*) as item_count,
        count(distinct seller_id) as seller_count,
        sum(price) as items_value,
        sum(freight_value) as freight_value,
        sum(item_revenue) as gross_revenue,
        max(_loaded_at) as _loaded_at
    from {{ ref('stg_order_items') }}
    where order_id in (select order_id from changed)
    group by order_id
),

payments as (
    select order_id, sum(payment_value) as payment_value, max(_loaded_at) as _loaded_at
    from {{ ref('stg_payments') }}
    where order_id in (select order_id from changed)
    group by order_id
),

-- Customer order sequence over *all* orders (cheap: a view over raw, one window).
sequence as (
    select
        order_id,
        row_number() over (partition by customer_unique_id order by purchased_at, order_id)
            as customer_order_number,
        min(purchased_at) over (partition by customer_unique_id) as customer_first_purchase_at
    from {{ ref('stg_orders') }}
)

select
    o.order_id,
    c.customer_sk,
    o.customer_unique_id,
    cast(to_char(o.purchased_at, 'YYYYMMDD') as integer) as purchase_date_key,
    o.order_status,
    o.purchased_at,
    o.approved_at,
    o.delivered_carrier_at,
    o.delivered_customer_at,
    o.estimated_delivery_date,
    coalesce(i.item_count, 0) as item_count,
    coalesce(i.seller_count, 0) as seller_count,
    coalesce(i.items_value, 0) as items_value,
    coalesce(i.freight_value, 0) as freight_value,
    coalesce(i.gross_revenue, 0) as gross_revenue,
    coalesce(p.payment_value, 0) as payment_value,
    o.order_status = 'delivered' and o.delivered_customer_at is not null as is_delivered,
    case when o.delivered_customer_at is not null
        then round(extract(epoch from o.delivered_customer_at - o.purchased_at) / 86400.0, 2)
    end as delivery_days,
    case when o.delivered_customer_at is not null
        then o.delivered_customer_at::date - o.estimated_delivery_date
    end as delay_days,
    coalesce(o.delivered_customer_at::date > o.estimated_delivery_date, false) as is_late,
    s.customer_order_number,
    s.customer_order_number = 1 as is_first_order,
    date_trunc('month', s.customer_first_purchase_at)::date as cohort_month,
    greatest(o._loaded_at, i._loaded_at, p._loaded_at) as _loaded_at
from orders o
join sequence s using (order_id)
left join items i using (order_id)
left join payments p using (order_id)
left join {{ ref('dim_customer') }} c
    on c.customer_unique_id = o.customer_unique_id
    and o.purchased_at >= c.valid_from
    and (c.valid_to is null or o.purchased_at < c.valid_to)
