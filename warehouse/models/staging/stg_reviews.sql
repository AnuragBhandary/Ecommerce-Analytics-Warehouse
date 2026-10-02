select
    review_id,
    order_id,
    score,
    nullif(trim(comment_title), '') as comment_title,
    nullif(trim(comment_message), '') as comment_message,
    sent_at,
    answered_at,
    _loaded_at
from {{ source('raw', 'order_reviews') }}
