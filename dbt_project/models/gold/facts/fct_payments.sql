-- Grain: one row per payment received. A separate business process from ordering: money
-- arrives at its own time (paid_at, not ordered_at), so cash-in by day or by payment method is
-- answered here, not from fct_orders.
--
-- payment_method stays on the fact as a plain attribute: three values with nothing else to
-- describe them don't justify a dim_payment_methods table.

with payments as (

    select * from {{ ref('stg_payments') }}

),

orders as (

    select order_id, customer_id from {{ ref('stg_orders') }}

)

select
    p.payment_id,
    p.order_id,                                         -- degenerate dimension, joins fct_orders

    md5(cast(o.customer_id as varchar))                 as customer_key,
    {{ to_reporting_date('p.paid_at') }}                as payment_date,

    p.payment_method,
    p.is_deleted,

    p.amount,

    p.paid_at                                           -- UTC, for time-of-day analysis

from payments p
left join orders o using (order_id)
