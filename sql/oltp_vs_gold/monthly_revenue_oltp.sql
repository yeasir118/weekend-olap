-- Question: How much revenue did we make each month? (OLAP-703: the OLTP version)
-- Runs on the production Postgres database. Same output as gold.mart_monthly_revenue.
--
-- Every rule below already lives in the Gold layer, written and tested once. Here each one has
-- to be known, remembered and re-typed by whoever writes the query:

with revenue_orders as (

    select
        o.id,
        o.user_id,
        o.total_amount,
        -- (1) Business months are Dhaka months. created_at is timestamptz; converting it to
        --     Dhaka local time moves ~25% of orders to a different day than UTC would.
        date_trunc('month', o.created_at at time zone 'Asia/Dhaka')::date   as month
    from orders o
    -- (2) The revenue definition: money received (paid or shipped)...
    where o.status in ('paid', 'shipped')
    -- (3) ...and the order isn't soft-deleted. Forget this line and revenue is overstated.
      and o.deleted_at is null

),

-- (4) Months with no sales must still appear, or month-over-month comparisons skip a gap.
months as (

    select generate_series(min(month), max(month), interval '1 month')::date as month
    from revenue_orders

)

select
    m.month,
    count(r.id)                                         as orders,
    coalesce(sum(r.total_amount), 0)                    as revenue,
    -- (5) AOV over the same set of orders as revenue, not over all orders placed.
    round(sum(r.total_amount) / nullif(count(r.id), 0), 2) as avg_order_value,
    count(distinct r.user_id)                           as customers
from months m
left join revenue_orders r using (month)
group by m.month
order by m.month;
