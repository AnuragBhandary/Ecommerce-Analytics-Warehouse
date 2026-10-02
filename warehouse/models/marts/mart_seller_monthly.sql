-- Seller x month scorecard. Feeds the Excel seller-performance workbook (Power Query) and the
-- Power BI seller page. An order with items from two sellers counts once for each.
with item_orders as (
    select
        i.seller_sk,
        i.order_id,
        date_trunc('month', o.purchased_at)::date as month,
        sum(i.item_revenue) as revenue,
        count(*) as items
    from {{ ref('fct_order_items') }} i
    join {{ ref('fct_orders') }} o using (order_id)
    where o.order_status not in ('canceled', 'unavailable')
    group by 1, 2, 3
)

select
    s.seller_id,
    s.state as seller_state,
    s.city as seller_city,
    io.month,
    count(distinct io.order_id) as orders,
    sum(io.items) as items,
    sum(io.revenue) as revenue,
    count(*) filter (where o.is_delivered) as delivered_orders,
    count(*) filter (where o.is_delivered and o.is_late) as late_orders,
    round(avg(r.score), 2) as avg_review_score
from item_orders io
join {{ ref('dim_seller') }} s using (seller_sk)
join {{ ref('fct_orders') }} o using (order_id)
left join (
    select order_id, avg(score) as score from {{ ref('fct_reviews') }} group by order_id
) r using (order_id)
group by 1, 2, 3, 4
