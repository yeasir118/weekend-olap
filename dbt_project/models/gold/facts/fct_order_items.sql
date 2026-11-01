-- Grain: one row per order line (one product within one order). The finer grain of the same
-- ordering process as fct_orders: use this table for anything sliced by product or category,
-- since an order-level row can't say which product its money came from.
--
-- Revenue follows fct_orders' definition, applied per line: a line counts when its order counts
-- (paid or shipped, not soft-deleted) and the line itself isn't soft-deleted. Sum `revenue` for
-- revenue questions; line_total is what the line was worth whatever the order's status.

with items as (

    select * from {{ ref('stg_order_items') }}

),

orders as (

    select * from {{ ref('fct_orders') }}

)

select
    i.order_item_id,
    i.order_id,                                         -- degenerate dimension, joins fct_orders

    md5(cast(i.product_id as varchar))                  as product_key,
    o.customer_key,                                     -- inherited from the order
    o.campaign_key,
    o.order_date,

    o.order_status,
    i.is_deleted,
    o.is_revenue and not i.is_deleted                   as is_revenue,

    i.quantity,
    i.unit_price,                                       -- price actually paid, not today's list price
    i.quantity * i.unit_price                           as line_total,
    case when o.is_revenue and not i.is_deleted
        then i.quantity * i.unit_price else 0
    end                                                 as revenue

from items i
left join orders o using (order_id)
