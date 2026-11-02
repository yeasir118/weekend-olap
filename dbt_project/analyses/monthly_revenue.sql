-- Question: How much revenue did we make each month, and from how many orders?
--
-- Written against the star schema (fact + date dimension) to show the basic star join:
-- filter and measure on the fact, group by an attribute of a dimension.
-- mart_monthly_revenue holds the same numbers, precomputed.

select
    d.month_start                   as month,
    count(*)                        as orders,
    sum(f.revenue)                  as revenue
from {{ ref('fct_orders') }} f
join {{ ref('dim_date') }} d on d.date_day = f.order_date
where f.is_revenue                  -- paid or shipped, not soft-deleted
group by d.month_start
order by month
