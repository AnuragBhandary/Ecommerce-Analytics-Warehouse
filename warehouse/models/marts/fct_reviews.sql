{{
    config(
        materialized='incremental',
        unique_key=['review_id', 'order_id'],
        incremental_strategy='merge',
        on_schema_change='fail',
    )
}}
-- Grain: one review survey per order. A survey row arrives when it is sent and is updated in
-- place (MERGE) when the customer answers it.
select
    r.review_id,
    r.order_id,
    cast(to_char(r.sent_at, 'YYYYMMDD') as integer) as sent_date_key,
    r.score,
    r.answered_at is not null as is_answered,
    r.comment_message is not null as has_comment,
    r.sent_at,
    r.answered_at,
    round(extract(epoch from r.answered_at - r.sent_at) / 3600.0, 1) as response_hours,
    r._loaded_at
from {{ ref('stg_reviews') }} r
{% if is_incremental() %}
where r._loaded_at >= (select coalesce(max(_loaded_at), '-infinity') from {{ this }})
{% endif %}
