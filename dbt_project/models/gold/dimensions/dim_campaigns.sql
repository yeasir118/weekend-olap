select
    md5(cast(campaign_id as varchar))           as campaign_key,
    campaign_id,

    campaign_name,
    channel,
    budget,                                     -- NULL when marketing's value was unreadable
    start_date,

    is_valid                                    as has_valid_details

from {{ ref('stg_campaigns') }}
