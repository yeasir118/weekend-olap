-- Question: Which marketing campaigns paid for themselves?
--
-- ROI = (attributed revenue - budget) / budget. 0.5 means the campaign brought back its
-- budget plus 50%; negative means it cost more than it brought in.
--
-- Caveats to state with the numbers:
--   - Attribution is last touch (the campaign_id on the order), and revenue isn't profit.
--   - ROI is NULL when marketing's budget couldn't be read (has_valid_details = false).
--   - Campaigns that haven't started yet show -100%: all budget, no orders so far.

select
    campaign_id,
    campaign_name,
    channel,
    start_date,
    budget,
    attributed_orders,
    attributed_revenue,
    cast((attributed_revenue - budget) / nullif(budget, 0) as decimal(10, 2))
                                    as roi,
    has_valid_details
from {{ ref('mart_campaign_performance') }}
order by roi desc nulls last
