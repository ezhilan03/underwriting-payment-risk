{% macro generate_alias_name(custom_alias_name=none, node=none) -%}
    {%- set base_name = custom_alias_name | trim if custom_alias_name is not none else node.name -%}
    {{ base_name }}{{ env_var('RISK_TABLE_SUFFIX', '') }}
{%- endmacro %}
