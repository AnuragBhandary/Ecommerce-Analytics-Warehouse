select
    {{ surrogate_key(['seller_id']) }} as seller_sk,
    seller_id,
    zip_code_prefix,
    city,
    state
from {{ ref('stg_sellers') }}
