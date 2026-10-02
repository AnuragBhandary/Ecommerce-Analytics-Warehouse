{# Use the configured schema name as-is (staging, marts, snapshots) instead of dbt's default
   "<target>_<custom>" concatenation: BI tools point at a stable schema called marts. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name is not none else target.schema }}
{%- endmacro %}
