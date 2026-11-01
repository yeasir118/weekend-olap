-- Invalid campaign rows are expected and kept in stg_campaigns, but someone should chase
-- marketing to fix them. Warning (not erroring) surfaces them on every build without
-- blocking it. Each returned row is one campaign to fix, with the reason.

{{ config(severity = 'warn') }}

select campaign_id, campaign_name, validation_errors, source_file
from {{ ref('stg_campaigns') }}
where not is_valid
