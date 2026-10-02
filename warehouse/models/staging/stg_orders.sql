select
    order_id,
    customer_id,
    customer_unique_id,
    order_status,
    purchased_at,
    approved_at,
    delivered_carrier_at,
    delivered_customer_at,
    estimated_delivery_date,
    _src_updated_at,
    _loaded_at
from {{ source('raw', 'orders') }}
