-- Known source-data issue: Olist has 8 orders marked delivered with no delivery date. Kept as a
-- warning (not an error) so it stays visible without blocking the pipeline.
{{ config(severity='warn') }}
select order_id
from {{ ref('fct_orders') }}
where order_status = 'delivered' and delivered_customer_at is null
