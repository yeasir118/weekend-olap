with customers as (

    select * from {{ ref('stg_users') }}

),

-- Order activity per customer: orders placed and not cancelled (pending counts, the customer
-- did order). Revenue is a separate definition, in fct_orders.
order_activity as (

    select
        customer_id,
        min({{ to_reporting_date('ordered_at') }})  as first_order_date,
        max({{ to_reporting_date('ordered_at') }})  as last_order_date,
        count(*)                                    as lifetime_orders
    from {{ ref('stg_orders') }}
    where order_status != 'cancelled'
      and not is_deleted
    group by customer_id

)

select
    md5(cast(c.customer_id as varchar))         as customer_key,
    c.customer_id,

    c.email,
    c.first_name,
    c.last_name,
    c.first_name || ' ' || c.last_name          as full_name,
    c.city,
    c.country,

    {{ to_reporting_date('c.created_at') }}     as signup_date,
    c.is_deleted,

    a.first_order_date,
    a.last_order_date,
    coalesce(a.lifetime_orders, 0)              as lifetime_orders

from customers c
left join order_activity a using (customer_id)
