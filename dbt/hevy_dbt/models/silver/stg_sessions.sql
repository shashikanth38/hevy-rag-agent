-- models/silver/stg_sessions.sql
-- One row per workout session.
--
-- The incremental filter is on _loaded_at, matching stg_sets. Filtering on
-- workout_date instead meant an edit to an older workout never reached this
-- model, which is exactly what the _loaded_at watermark exists to catch.

{{
    config(
        materialized = 'incremental',
        unique_key   = ['workout_id'],
        on_schema_change = 'sync_all_columns'
    )
}}

WITH sets AS (
    SELECT * FROM {{ ref('stg_sets') }}

    {% if is_incremental() %}
        WHERE _loaded_at > (SELECT MAX(_loaded_at) FROM {{ this }})
    {% endif %}
)

SELECT
    workout_id,
    MAX(workout_title)                                  AS workout_title,
    MIN(workout_date)                                   AS workout_date,
    MIN(week_start)                                     AS week_start,
    MAX(year)                                           AS year,
    MAX(month)                                          AS month,
    MIN(start_time)                                     AS start_time,
    MAX(end_time)                                       AS end_time,
    MAX(session_duration_min)                           AS session_duration_min,

    COUNT(*)                                            AS total_sets,
    COUNT(DISTINCT exercise_title)                      AS total_exercises,
    COUNT(DISTINCT primary_muscle)                      AS muscles_trained,
    SUM(tonnage)                                        AS total_tonnage,
    SUM(reps)                                           AS total_reps,
    SUM(CASE WHEN is_failure_set THEN 1 ELSE 0 END)     AS failure_sets,
    SUM(CASE WHEN is_dropset     THEN 1 ELSE 0 END)     AS dropset_sets,
    SUM(CASE WHEN is_superset    THEN 1 ELSE 0 END)     AS superset_sets,

    -- work rate: tonnage per minute of session time
    ROUND(
        SUM(tonnage) / NULLIF(MAX(session_duration_min), 0)
    , 1)                                                AS tonnage_per_min,

    -- gym is constant within a session; MAX picks it deterministically
    MAX(gym)                                            AS gym,
    MAX(gym_detail)                                     AS gym_detail,
    MAX(_loaded_at)                                     AS _loaded_at
FROM sets
WHERE workout_date IS NOT NULL
GROUP BY workout_id
