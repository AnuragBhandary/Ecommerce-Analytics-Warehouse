-- Monthly acquisition cohorts: of the customers whose first purchase was in cohort_month, how
-- many purchased again N months later. Feeds the Metabase cohort-retention dashboard.
-- A cohort is the month of the customer's first *successful* order: fct_orders.cohort_month
-- counts canceled first attempts too, which would leave month 0 short of the full cohort (the
-- assert_customer_cohort_starts_full test caught exactly that).
with orders as (
    select customer_unique_id, date_trunc('month', purchased_at)::date as active_month
    from {{ ref('fct_orders') }}
    where order_status not in ('canceled', 'unavailable')
),

activity as (
    select distinct
        customer_unique_id,
        min(active_month) over (partition by customer_unique_id) as cohort_month,
        active_month
    from orders
),

sized as (
    select cohort_month, count(distinct customer_unique_id) as cohort_size
    from activity
    group by 1
)

select
    a.cohort_month,
    (extract(year from age(a.active_month, a.cohort_month)) * 12
        + extract(month from age(a.active_month, a.cohort_month)))::int as months_since_first,
    count(distinct a.customer_unique_id) as active_customers,
    s.cohort_size,
    round(count(distinct a.customer_unique_id)::numeric / s.cohort_size, 4) as retention_rate
from activity a
join sized s using (cohort_month)
group by a.cohort_month, 2, s.cohort_size
