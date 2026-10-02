-- Known source-data issue: 74 Olist review surveys are dated before their order was placed (up to
-- 111 days). Reported as a warning so the anomaly stays visible; analyses of review response
-- time should exclude these rows.
{{ config(severity='warn') }}
select r.review_id, r.order_id, r.sent_at, o.purchased_at
from {{ ref('fct_reviews') }} r
join {{ ref('fct_orders') }} o using (order_id)
where r.sent_at::date < o.purchased_at::date
