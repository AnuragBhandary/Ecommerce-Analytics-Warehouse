select
    order_id,
    payment_sequential,
    payment_type,
    installments,
    payment_value,
    _loaded_at
from {{ source('raw', 'order_payments') }}
