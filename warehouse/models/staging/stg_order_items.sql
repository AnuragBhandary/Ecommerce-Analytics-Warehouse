select
    order_id,
    order_item_id,
    product_id,
    seller_id,
    shipping_limit_at,
    price,
    freight_value,
    price + freight_value as item_revenue,
    _loaded_at
from {{ source('raw', 'order_items') }}
