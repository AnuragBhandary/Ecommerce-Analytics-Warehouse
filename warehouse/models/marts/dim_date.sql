-- Calendar covering the whole dataset (Olist runs 2016-09 to 2018-10) plus slack on both sides.
-- Power BI marks this as its date table; every fact carries an integer yyyymmdd key into it.
select
    cast(to_char(d, 'YYYYMMDD') as integer) as date_key,
    d::date as date,
    extract(year from d)::int as year,
    extract(quarter from d)::int as quarter,
    extract(month from d)::int as month,
    to_char(d, 'Mon') as month_name,
    to_char(d, 'YYYY-MM') as year_month,
    date_trunc('month', d)::date as month_start,
    extract(isodow from d)::int as day_of_week,
    to_char(d, 'Dy') as day_name,
    extract(isodow from d) in (6, 7) as is_weekend
from generate_series('2016-01-01'::date, '2019-12-31'::date, interval '1 day') as d
