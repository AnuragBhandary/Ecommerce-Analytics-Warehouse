select customer_unique_id
from {{ ref('dim_customer') }}
group by customer_unique_id
having count(*) filter (where is_current) <> 1
