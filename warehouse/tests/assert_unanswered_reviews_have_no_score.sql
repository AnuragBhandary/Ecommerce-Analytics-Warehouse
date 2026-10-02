select review_id, order_id
from {{ ref('fct_reviews') }}
where (is_answered and score is null) or (not is_answered and score is not null)
