select
    md5(cast(product_id as varchar))            as product_key,
    product_id,

    sku,
    product_name,
    category,

    -- Today's list price. What a customer actually paid is fct_order_items.unit_price.
    unit_price                                  as current_unit_price,

    {{ to_reporting_date('created_at') }}       as launch_date,
    is_deleted                                  as is_discontinued

from {{ ref('stg_products') }}
