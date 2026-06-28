-- models/gold/weekly_volume.sql
-- Weekly volume per muscle group per gym
-- Primary input for fatigue and overload analysis

{{
    config(
        materialized = 'table',
        on_schema_change = 'sync_all_columns'
    )
}}

WITH sets AS (
    SELECT
        s.*,
        COALESCE(et.primary_muscle_group, 'unknown')        AS primary_muscle,
        COALESCE(et.equipment_category,   'unknown')        AS equipment_category,
        et.is_custom
    FROM {{ ref('stg_sets') }} s
    LEFT JOIN {{ source('bronze', 'dim_exercise_templates') }} et
        ON LOWER(s.exercise_title) = LOWER(et.title)
)

SELECT
    year,
    week_number,
    week_start,
    gym,
    gym_detail,
    primary_muscle,
    equipment_category,
    COUNT(DISTINCT workout_date)            AS training_days,
    COUNT(DISTINCT exercise_title)          AS unique_exercises,
    COUNT(*)                                AS total_sets,
    SUM(reps)                               AS total_reps,
    SUM(tonnage)                            AS total_tonnage,
    AVG(weight_kg)                          AS avg_weight_kg,
    MAX(weight_kg)                          AS max_weight_kg,
    SUM(CASE WHEN is_failure_set
        THEN 1 ELSE 0 END)                  AS failure_sets,
    -- week over week tonnage change
    LAG(SUM(tonnage)) OVER (
        PARTITION BY gym_detail, primary_muscle
        ORDER BY year, week_number
    )                                       AS prev_week_tonnage,
    ROUND(
        (SUM(tonnage) - LAG(SUM(tonnage)) OVER (
            PARTITION BY gym_detail, primary_muscle
            ORDER BY year, week_number
        )) / NULLIF(LAG(SUM(tonnage)) OVER (
            PARTITION BY gym_detail, primary_muscle
            ORDER BY year, week_number
        ), 0) * 100, 1
    )                                       AS tonnage_pct_change
FROM sets
GROUP BY
    year, week_number, week_start,
    gym, gym_detail,
    primary_muscle, equipment_category