-- models/silver/stg_measurements.sql
{{
    config(
        materialized = 'incremental',
        unique_key   = ['measurement_date'],
        on_schema_change = 'sync_all_columns'
    )
}}

SELECT
    TRY_TO_DATE(measurement_date)       AS measurement_date,
    TRY_TO_DECIMAL(weight_kg, 5, 2)    AS weight_kg,
    TRY_TO_DECIMAL(body_fat_pct, 5, 2) AS body_fat_pct,
    _loaded_at
FROM {{ source('bronze', 'raw_measurements') }}
WHERE TRY_TO_DATE(measurement_date) IS NOT NULL

{% if is_incremental() %}
    AND TRY_TO_DATE(measurement_date)
        > (SELECT MAX(measurement_date) FROM {{ this }})
{% endif %}
