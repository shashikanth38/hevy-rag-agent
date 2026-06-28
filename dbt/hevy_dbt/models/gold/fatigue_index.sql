-- models/gold/fatigue_index.sql
-- 7-day vs 28-day rolling tonnage ratio
-- > 1.2 = potential overtraining signal
-- < 0.8 = deload or undertraining

{{
    config(
        materialized = 'table',
        on_schema_change = 'sync_all_columns'
    )
}}

WITH daily AS (
    SELECT
        workout_date,
        gym,
        gym_detail,
        SUM(tonnage)                AS daily_tonnage,
        COUNT(*)                    AS daily_sets,
        COUNT(DISTINCT exercise_title) AS daily_exercises
    FROM {{ ref('stg_sets') }}
    GROUP BY workout_date, gym, gym_detail
),

with_rolling AS (
    SELECT
        workout_date,
        gym,
        gym_detail,
        daily_tonnage,
        daily_sets,
        daily_exercises,
        -- 7 day rolling average
        AVG(daily_tonnage) OVER (
            ORDER BY workout_date
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        )                           AS rolling_7d_avg_tonnage,
        -- 28 day rolling average
        AVG(daily_tonnage) OVER (
            ORDER BY workout_date
            ROWS BETWEEN 27 PRECEDING AND CURRENT ROW
        )                           AS rolling_28d_avg_tonnage
    FROM daily
)

SELECT
    workout_date,
    gym,
    gym_detail,
    daily_tonnage,
    daily_sets,
    daily_exercises,
    ROUND(rolling_7d_avg_tonnage,  2)   AS rolling_7d_avg_tonnage,
    ROUND(rolling_28d_avg_tonnage, 2)   AS rolling_28d_avg_tonnage,
    ROUND(
        rolling_7d_avg_tonnage /
        NULLIF(rolling_28d_avg_tonnage, 0)
    , 2)                                AS fatigue_index,
    CASE
        WHEN rolling_7d_avg_tonnage / NULLIF(rolling_28d_avg_tonnage, 0) > 1.2
            THEN 'overreaching'
        WHEN rolling_7d_avg_tonnage / NULLIF(rolling_28d_avg_tonnage, 0) < 0.8
            THEN 'undertraining'
        ELSE 'optimal'
    END                                 AS training_status
FROM with_rolling