with source as (

    select * from {{ source('bronze', 'raw_orders') }}

),

renamed as (

    select
        cast(id as bigint)                    as order_id,
        cast(user_id as bigint)               as customer_id,
        cast(campaign_id as integer)          as campaign_id,

        lower(trim(status))                   as order_status,
        cast(total_amount as decimal(12, 2))  as order_total,

        deleted_at is not null                as is_deleted,

        created_at at time zone 'UTC'         as ordered_at,
        updated_at at time zone 'UTC'         as updated_at,
        deleted_at at time zone 'UTC'         as deleted_at,
        ingested_at at time zone 'UTC'        as ingested_at

    from source

)

select * from renamed
