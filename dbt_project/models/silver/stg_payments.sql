with source as (

    select * from {{ source('bronze', 'raw_payments') }}

),

renamed as (

    select
        cast(id as bigint)                    as payment_id,
        cast(order_id as bigint)              as order_id,

        lower(trim(method))                   as payment_method,
        cast(amount as decimal(12, 2))        as amount,

        deleted_at is not null                as is_deleted,

        created_at at time zone 'UTC'         as paid_at,
        updated_at at time zone 'UTC'         as updated_at,
        deleted_at at time zone 'UTC'         as deleted_at,
        ingested_at at time zone 'UTC'        as ingested_at

    from source

)

select * from renamed
