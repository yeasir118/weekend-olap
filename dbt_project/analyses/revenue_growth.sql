-- Question: Is revenue growing month over month, and what is the running total for the year?
--
-- Window functions compute across rows without collapsing them:
--   lag(revenue) over (order by month)        the previous month's value, on this row
--   sum(revenue) over (partition by year ...)  a running total that restarts each year
--
-- The current month is still in progress, so its growth would look like a collapse
-- (3 days vs a full month). It's flagged rather than dropped so the reader decides.

with monthly as (

    select
        month,
        revenue,
        month = date_trunc('month', current_date)   as is_partial_month
    from {{ ref('mart_monthly_revenue') }}

)

select
    month,
    revenue,
    lag(revenue) over (order by month)              as previous_month_revenue,
    cast(revenue / nullif(lag(revenue) over (order by month), 0) - 1 as decimal(10, 4))
                                                    as mom_growth,
    sum(revenue) over (partition by year(month) order by month)
                                                    as revenue_year_to_date,
    is_partial_month
from monthly
order by month
