-- Question: Which products sell best, and which is the top seller in each category?
--
-- Product questions need the order-line fact: fct_orders is one row per order and can't say
-- which product the money came from (decision D1). Only revenue orders count, so units sold
-- don't include cancelled or unpaid orders.
-- rank() is a window function: it ranks rows within each category without collapsing them.

select
    p.category,
    p.product_name,
    count(distinct f.order_id)      as orders,
    sum(f.quantity)                 as units_sold,
    sum(f.revenue)                  as revenue,
    rank() over (partition by p.category order by sum(f.revenue) desc)
                                    as rank_in_category,
    p.is_discontinued
from {{ ref('fct_order_items') }} f
join {{ ref('dim_products') }} p using (product_key)
where f.is_revenue
group by p.product_key, p.category, p.product_name, p.is_discontinued
order by p.category, rank_in_category
