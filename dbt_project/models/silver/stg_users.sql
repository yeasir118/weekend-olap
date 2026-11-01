with source as (

    select * from {{ source('bronze', 'raw_users') }}

),

renamed as (

    select
        cast(id as bigint)                    as customer_id,

        lower(trim(email))                    as email,
        trim(first_name)                      as first_name,
        trim(last_name)                       as last_name,
        trim(city)                            as city,
        trim(country)                         as country,

        deleted_at is not null                as is_deleted,

        created_at at time zone 'UTC'         as created_at,
        updated_at at time zone 'UTC'         as updated_at,
        deleted_at at time zone 'UTC'         as deleted_at,
        ingested_at at time zone 'UTC'        as ingested_at

    from source

)

select * from renamed
