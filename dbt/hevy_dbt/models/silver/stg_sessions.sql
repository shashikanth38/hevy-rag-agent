-- models/silver/stg_sessions.sql
{{
    config(
        materialized = 'incremental',
        unique_key   = ['workout_date', 'workout_title', 'start_time'],
        on_schema_change = 'sync_all_columns'
    )
}}

WITH sets AS (
    SELECT * FROM {{ ref('stg_sets') }}

    {% if is_incremental() %}
        WHERE workout_date > (SELECT MAX(workout_date) FROM {{ this }})
    {% endif %}
),

aggregated AS (
    SELECT
        workout_title,
        workout_date,
        start_time,
        end_time,
        year,
        month,
        week_number,
        week_start,
        gym,
        gym_detail,
        MAX(session_duration_min)                           AS session_duration_min,
        COUNT(*)                                            AS total_sets,
        COUNT(DISTINCT exercise_title)                      AS total_exercises,
        SUM(tonnage)                                        AS total_tonnage,
        SUM(reps)                                           AS total_reps,
        SUM(CASE WHEN is_failure_set THEN 1 ELSE 0 END)    AS failure_sets,
        SUM(CASE WHEN is_superset    THEN 1 ELSE 0 END)    AS superset_sets,
        MAX(_loaded_at)                                     AS _loaded_at
    FROM sets
    GROUP BY
        workout_title, workout_date, start_time, end_time,
        year, month, week_number, week_start,
        gym, gym_detail
)

-- deduplicate boundary date duplicates
-- keep the gym_detail that is not null, preferring the more specific one
SELECT *
FROM aggregated
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY workout_date, workout_title, start_time
    ORDER BY gym_detail NULLS LAST, gym NULLS LAST
) = 1