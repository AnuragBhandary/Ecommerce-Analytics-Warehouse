-- SCD Type 2: one row per address version of a customer (customer_unique_id = the person).
-- The first version is open-ended at the start (valid_from 1900-01-01) so every order joins to a
-- version even when the snapshot first saw the customer after their earliest order.
select
    {{ surrogate_key(['customer_unique_id', 'dbt_valid_from']) }} as customer_sk,
    customer_unique_id,
    zip_code_prefix,
    city,
    state,
    dbt_valid_from as address_effective_at,
    case
        when row_number() over (partition by customer_unique_id order by dbt_valid_from) = 1
            then timestamp '1900-01-01'
        else dbt_valid_from
    end as valid_from,
    dbt_valid_to as valid_to,
    dbt_valid_to is null as is_current,
    count(*) over (partition by customer_unique_id) as version_count
from {{ ref('snap_customers') }}
