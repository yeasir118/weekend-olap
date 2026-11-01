{#
    The business date of a UTC timestamp from Silver, in the reporting time zone (var
    reporting_timezone). An order at 2026-03-01 20:30 UTC is on 2026-03-02 in Dhaka.

        {{ to_reporting_date('ordered_at') }}  ->  date
#}
{% macro to_reporting_date(utc_timestamp) %}
    cast(({{ utc_timestamp }} at time zone 'UTC') at time zone '{{ var("reporting_timezone") }}' as date)
{% endmacro %}
