{#
    Use the custom schema name as-is (silver, gold) instead of dbt's default
    <target_schema>_<custom_schema> (main_silver). The prefix exists so developers sharing
    a warehouse don't overwrite each other; this project has one user, so it's just noise.
    Models without a custom schema fall back to the target schema from profiles.yml.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
