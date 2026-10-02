-- Each customer's address versions must tile time with no gaps or overlaps: every version's
-- valid_to is exactly the next version's valid_from.
select customer_unique_id, valid_to, next_valid_from
from (
    select
        customer_unique_id,
        valid_to,
        lead(address_effective_at) over (
            partition by customer_unique_id order by address_effective_at
        ) as next_valid_from
    from {{ ref('dim_customer') }}
) v
where valid_to is distinct from next_valid_from
