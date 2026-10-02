-- Denormalized attributes must not drift: every item carries its order's current status.
select i.order_id, i.order_item_id, i.order_status as item_status, o.order_status
from {{ ref('fct_order_items') }} i
join {{ ref('fct_orders') }} o using (order_id)
where i.order_status <> o.order_status
