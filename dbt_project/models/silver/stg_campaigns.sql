with source as (

    select * from {{ source('bronze', 'raw_campaigns') }}

),

parsed as (

    select
        try_cast(trim(campaign_id) as integer)       as campaign_id,
        trim(campaign_name)                          as campaign_name,
        lower(trim(channel))                         as channel,

        -- "5,000": strip commas only when they're clearly thousands separators, so a
        -- value like "5,5" is rejected instead of silently becoming 55.
        try_cast(
            case
                when regexp_full_match(trim(budget), '\d{1,3}(,\d{3})+(\.\d+)?')
                    then replace(trim(budget), ',', '')
                else trim(budget)
            end
        as decimal(12, 2))                           as budget,

        -- Only the agreed ISO format. "31/12/2026" isn't guessed: the same rule would read
        -- "01/02/2026" as 1 Feb, when marketing may have meant 2 Jan.
        try_strptime(trim(start_date), '%Y-%m-%d')::date as start_date,

        budget                                       as budget_raw,
        start_date                                   as start_date_raw,
        source_file,
        delivered_on,
        ingested_at at time zone 'UTC'               as ingested_at

    from source

),

validated as (

    select
        *,
        nullif(concat_ws('; ',
            case when budget is null then 'budget ' || coalesce('''' || budget_raw || ''' is not a number', 'is missing') end,
            case when start_date is null then 'start_date ' || coalesce('''' || start_date_raw || ''' is not YYYY-MM-DD', 'is missing') end
        ), '')                                       as validation_errors

    from parsed
    -- A row without a usable id can't be a campaign at all. It stays visible in Bronze.
    where campaign_id is not null

),

latest_delivery as (

    -- Deliveries are deltas: a campaign reappears in a later file when marketing changes it.
    -- The latest delivery is marketing's current truth, even if its values are invalid.
    select *
    from validated
    qualify row_number() over (partition by campaign_id order by delivered_on desc) = 1

)

select
    campaign_id,
    campaign_name,
    channel,
    budget,
    start_date,

    validation_errors is null                        as is_valid,
    validation_errors,

    source_file,
    delivered_on,
    ingested_at

from latest_delivery
