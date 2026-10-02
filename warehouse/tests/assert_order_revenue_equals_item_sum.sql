-- The order-grain fact must agree with the item-grain fact, order by order, to the cent.
select o.order_id, o.gross_revenue, i.revenue
from {{ ref('fct_orders') }} o
left join (
    select order_id, sum(item_revenue) as revenue from {{ ref('fct_order_items') }} group by 1
) i using (order_id)
where o.gross_revenue <> coalesce(i.revenue, 0)
