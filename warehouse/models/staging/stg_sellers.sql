select
    seller_id,
    zip_code_prefix,
    initcap(city) as city,
    upper(state) as state,
    _loaded_at
from {{ source('raw', 'sellers') }}
