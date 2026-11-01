-- One row per calendar day. A fixed range rather than min/max of the orders: the table is the
-- same on every build, covers future dates (campaigns starting next year), and doesn't depend
-- on any fact table. Widen the range here when needed; it's cheap (~4,000 rows).
{% set start_date = '2020-01-01' %}
{% set end_date = '2031-01-01' %}  {# exclusive #}

with days as (

    select cast(range as date) as date_day
    from range(date '{{ start_date }}', date '{{ end_date }}', interval 1 day)

)

select
    date_day,

    year(date_day)                            as year,
    quarter(date_day)                         as quarter,
    month(date_day)                           as month,
    monthname(date_day)                       as month_name,
    cast(date_trunc('month', date_day) as date) as month_start,

    cast(date_trunc('week', date_day) as date)  as week_start,       -- ISO week: starts Monday
    day(date_day)                             as day_of_month,
    isodow(date_day)                          as day_of_week,      -- ISO: 1 = Monday ... 7 = Sunday
    dayname(date_day)                         as day_name,

    -- Saturday/Sunday, the international default. Business calendars differ (Bangladesh's
    -- weekend is Friday/Saturday): this is the one place to change it, which is part of why
    -- a date dimension exists.
    isodow(date_day) in (6, 7)                as is_weekend

from days
