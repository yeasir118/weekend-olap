-- Question: Who are our 10 most valuable customers by lifetime revenue?
--
-- Grouped by customer_key, not by name: two customers can share a name, and grouping by
-- name would silently merge them. The name is only for display.
-- rank() + qualify instead of limit 10: customers tied at 10th place all stay in, rather
-- than one of them being dropped arbitrarily. (qualify filters on a window function, the
-- way having filters on an aggregate.)

select
    rank() over (order by sum(f.revenue) desc)  as revenue_rank,
    c.customer_id,
    c.full_name,
    c.country,
    count(*)                                    as orders,
    sum(f.revenue)                              as lifetime_revenue,
    c.is_deleted                                as account_closed
from {{ ref('fct_orders') }} f
join {{ ref('dim_customers') }} c using (customer_key)
where f.is_revenue
group by c.customer_key, c.customer_id, c.full_name, c.country, c.is_deleted
qualify revenue_rank <= 10
order by revenue_rank
