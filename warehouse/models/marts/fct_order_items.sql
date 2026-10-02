{{
    config(
        materialized='incremental',
        unique_key=['order_id', 'order_item_id'],
        incremental_strategy='merge',
        on_schema_change='fail',
    )
}}
-- Grain: one row per item in an order. Revenue = price + freight (what the customer was charged
-- for the item). Payments differ from this by vouchers and card interest; see DESIGN.md.
--
-- Incremental: an item is reprocessed when the item *or its order* changed. Items never change
-- after insert, but they carry the order's status, and the first version of this model only
-- looked at the item's own _loaded_at, so statuses froze at 'created'. The full-data proof run
-- caught it (incremental result != --full-refresh); assert_item_status_matches_order now
-- catches it on every run.
select
    i.order_id,
    i.order_item_id,
    {{ surrogate_key(['i.product_id']) }} as product_sk,
    {{ surrogate_key(['i.seller_id']) }} as seller_sk,
    cast(to_char(o.purchased_at, 'YYYYMMDD') as integer) as purchase_date_key,
    o.order_status,
    i.shipping_limit_at,
    i.price,
    i.freight_value,
    i.item_revenue,
    greatest(i._loaded_at, o._loaded_at) as _loaded_at
from {{ ref('stg_order_items') }} i
join {{ ref('stg_orders') }} o using (order_id)
{% if is_incremental() %}
where i._loaded_at >= (select coalesce(max(_loaded_at), '-infinity') from {{ this }})
   or o._loaded_at >= (select coalesce(max(_loaded_at), '-infinity') from {{ this }})
{% endif %}
