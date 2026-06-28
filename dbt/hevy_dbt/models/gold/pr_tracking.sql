-- models/gold/pr_tracking.sql
-- Personal records per exercise per gym
-- Cross-gym comparable flag based on equipment type

{{
    config(
        materialized = 'table',
        on_schema_change = 'sync_all_columns'
    )
}}

WITH sets AS (
    SELECT
        s.*,
        COALESCE(et.primary_muscle_group, 'unknown')    AS primary_muscle,
        COALESCE(et.equipment_category,   'unknown')    AS equipment_category,
        -- cross gym comparable only for standardised equipment
        et.equipment_category IN (
            'barbell', 'dumbbell', 'smith_machine', 'weighted_bodyweight'
        )                                               AS is_cross_gym_comparable
    FROM {{ ref('stg_sets') }} s
    LEFT JOIN {{ source('bronze', 'dim_exercise_templates') }} et
        ON LOWER(s.exercise_title) = LOWER(et.title)
    WHERE weight_kg > 0
),

-- PR per exercise per gym per date
daily_max AS (
    SELECT
        exercise_title,
        primary_muscle,
        equipment_category,
        is_cross_gym_comparable,
        gym,
        gym_detail,
        workout_date,
        MAX(weight_kg)  AS max_weight_kg,
        MAX(reps)       AS max_reps,
        MAX(tonnage)    AS max_tonnage
    FROM sets
    GROUP BY
        exercise_title, primary_muscle, equipment_category,
        is_cross_gym_comparable, gym, gym_detail, workout_date
),

-- cumulative PR over time
with_pr AS (
    SELECT
        *,
        MAX(max_weight_kg) OVER (
            PARTITION BY exercise_title, gym_detail
            ORDER BY workout_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                               AS cumulative_pr_kg,
        ROW_NUMBER() OVER (
            PARTITION BY exercise_title, gym_detail
            ORDER BY max_weight_kg DESC, workout_date DESC
        )                                               AS pr_rank
    FROM daily_max
)

SELECT
    exercise_title,
    primary_muscle,
    equipment_category,
    is_cross_gym_comparable,
    gym,
    gym_detail,
    workout_date,
    max_weight_kg,
    max_reps,
    max_tonnage,
    cumulative_pr_kg,
    (max_weight_kg = cumulative_pr_kg)                  AS is_pr,
    pr_rank
FROM with_pr