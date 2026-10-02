-- Month 0 of every cohort is, by definition, the whole cohort.
select cohort_month
from {{ ref('mart_customer_cohorts') }}
where months_since_first = 0 and active_customers <> cohort_size
