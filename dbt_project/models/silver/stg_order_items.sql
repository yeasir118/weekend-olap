with source as (

    select * from {{ source('bronze', 'raw_order_items') }}

),

renamed as (

    select
        cast(id as bigint)                    as order_item_id,
        cast(order_id as bigint)              as order_id,
        cast(product_id as bigint)            as product_id,

        cast(quantity as integer)             as quantity,
        cast(unit_price as decimal(10, 2))    as unit_price,

        deleted_at is not null                as is_deleted,

        created_at at time zone 'UTC'         as created_at,
        updated_at at time zone 'UTC'         as updated_at,
        deleted_at at time zone 'UTC'         as deleted_at,
        ingested_at at time zone 'UTC'        as ingested_at

    from source

)

select * from renamed
