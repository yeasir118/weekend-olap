-- One row per calendar month: the monthly revenue report, ready to read without joins.
--
-- Every column counts revenue orders only (fct_orders.is_revenue), so the numbers agree with
-- each other: avg_order_value = revenue / orders. Months between the first and last order with
-- no sales still get a row with zeros, so month-over-month comparisons (lag()) never skip a gap.

with months as (

    select distinct month_start
    from {{ ref('dim_date') }}
    where month_start between (select date_trunc('month', min(order_date)) from {{ ref('fct_orders') }})
                          and (select date_trunc('month', max(order_date)) from {{ ref('fct_orders') }})

),

monthly as (

    select
        d.month_start,
        count(*)                        as orders,
        sum(f.revenue)                  as revenue,
        count(distinct f.customer_key)  as customers
    from {{ ref('fct_orders') }} f
    join {{ ref('dim_date') }} d on d.date_day = f.order_date
    where f.is_revenue
    group by d.month_start

)

select
    m.month_start                               as month,
    coalesce(s.orders, 0)                       as orders,
    coalesce(s.revenue, 0)                      as revenue,
    -- Dividing decimals gives a double in DuckDB; cast back to money. NULL in a month with no orders.
    cast(s.revenue / s.orders as decimal(12, 2)) as avg_order_value,
    coalesce(s.customers, 0)                    as customers
from months m
left join monthly s using (month_start)
order by m.month_start
