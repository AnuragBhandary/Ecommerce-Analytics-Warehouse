{# Generic tests, written here instead of pulling in dbt_utils, so the project has no package
   dependency and `dbt deps` never needs the network. #}

{% test non_negative(model, column_name) %}
select {{ column_name }} from {{ model }} where {{ column_name }} < 0
{% endtest %}

{% test between(model, column_name, min_value, max_value) %}
select {{ column_name }} from {{ model }}
where {{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }}
{% endtest %}

{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }} from {{ model }}
group by {{ columns | join(', ') }} having count(*) > 1
{% endtest %}
