-- Grain: one row per order placed, whatever its status. Cancelled and soft-deleted orders are
-- kept and flagged rather than filtered, so cancellation rates stay answerable.
--
-- Revenue definition: an order counts as revenue when the money has been received (status
-- paid or shipped) and the order hasn't been soft-deleted. Pending orders aren't revenue yet;
-- cancelled ones never will be. Use the `revenue` column, not order_total, for revenue
-- questions: it's already 0 for orders that don't count, so a plain sum() is correct.

with orders as (

    select * from {{ ref('stg_orders') }}

),

items as (

    select
        order_id,
        count(*)        as line_count,
        sum(quantity)   as item_count
    from {{ ref('stg_order_items') }}
    group by order_id

),

flagged as (

    select
        *,
        order_status in ('paid', 'shipped') and not is_deleted as is_revenue
    from orders

)

select
    o.order_id,                                         -- degenerate dimension: no dim_orders

    md5(cast(o.customer_id as varchar))                 as customer_key,
    case when o.campaign_id is not null
        then md5(cast(o.campaign_id as varchar))
    end                                                 as campaign_key,
    {{ to_reporting_date('o.ordered_at') }}             as order_date,

    o.order_status,
    o.is_deleted,
    o.is_revenue,

    coalesce(i.line_count, 0)                           as line_count,
    coalesce(i.item_count, 0)                           as item_count,
    o.order_total,
    case when o.is_revenue then o.order_total else 0 end as revenue,

    o.ordered_at                                        -- UTC, for time-of-day analysis

from flagged o
left join items i using (order_id)
