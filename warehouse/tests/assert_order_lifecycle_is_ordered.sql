-- An order can't be approved before it was placed.
select order_id, purchased_at, approved_at
from {{ ref('fct_orders') }}
where approved_at < purchased_at
