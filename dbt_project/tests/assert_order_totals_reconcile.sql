-- An order's recorded total must equal the sum of its lines. Two facts at two grains of the same
-- process have to agree, or "revenue by month" and "revenue by product" give different totals.
-- Returns the orders that don't reconcile; the full outer join also catches lines whose order
-- is missing and orders with no lines.

with orders as (

    select order_id, order_total from {{ ref('fct_orders') }}

),

lines as (

    select order_id, sum(line_total) as lines_total
    from {{ ref('fct_order_items') }}
    group by order_id

)

select
    coalesce(o.order_id, l.order_id)    as order_id,
    o.order_total,
    l.lines_total
from orders o
full outer join lines l using (order_id)
where o.order_total is distinct from coalesce(l.lines_total, 0)
