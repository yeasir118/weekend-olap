-- One row per campaign: what each campaign brought in against what it cost.
--
-- Attribution is the app's own: an order belongs to the campaign on its campaign_id (last
-- touch, set at checkout). Only revenue orders count. Campaigns with no orders yet still
-- appear, with zeros, so marketing sees the whole list.

with attributed as (

    select
        campaign_key,
        count(*)            as attributed_orders,
        sum(revenue)        as attributed_revenue
    from {{ ref('fct_orders') }}
    where is_revenue
      and campaign_key is not null
    group by campaign_key

)

select
    c.campaign_id,
    c.campaign_name,
    c.channel,
    c.start_date,
    c.budget,
    c.has_valid_details,

    coalesce(a.attributed_orders, 0)                    as attributed_orders,
    coalesce(a.attributed_revenue, 0)                   as attributed_revenue,

    -- Revenue per unit of budget (2.5 = 2.5x the spend came back). NULL when the budget is
    -- unknown (marketing's value couldn't be read) or zero.
    cast(coalesce(a.attributed_revenue, 0) / nullif(c.budget, 0) as decimal(10, 2))
                                                        as revenue_to_budget

from {{ ref('dim_campaigns') }} c
left join attributed a using (campaign_key)
order by c.campaign_id
