{# md5 over the natural key parts, '|'-separated so ('ab','c') and ('a','bc') never collide. #}
{% macro surrogate_key(parts) -%}
    md5({% for p in parts %}coalesce(cast({{ p }} as text), '~'){% if not loop.last %} || '|' || {% endif %}{% endfor %})
{%- endmacro %}
