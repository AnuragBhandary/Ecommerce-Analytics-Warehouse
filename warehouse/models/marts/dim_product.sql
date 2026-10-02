select
    {{ surrogate_key(['product_id']) }} as product_sk,
    product_id,
    category,
    category_pt,
    photos_qty,
    weight_g,
    length_cm * height_cm * width_cm as volume_cm3
from {{ ref('stg_products') }}
