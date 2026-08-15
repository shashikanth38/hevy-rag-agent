-- models/gold/strength_progression.sql

{{
    config(
        materialized = 'table',
        on_schema_change = 'sync_all_columns'
    )
}}

WITH daily_sets AS (
    SELECT
        workout_date,
        exercise_title,
        gym,
        gym_detail,
        week_start,
        year,
        week_number,
        exercise_notes,
        weight_kg,
        reps,
        tonnage,
        MAX(weight_kg) OVER (
            PARTITION BY exercise_title, gym_detail, workout_date
        )                                               AS day_max_weight,

        -- equipment mismatch detection
        CASE
            WHEN LOWER(exercise_notes) LIKE '%smith%'
            AND  LOWER(exercise_title) LIKE '%barbell%'
            THEN TRUE ELSE FALSE
        END                                             AS equipment_mismatch,

        -- corrected exercise title
        CASE
            WHEN LOWER(exercise_notes) LIKE '%smith%'
            AND  LOWER(exercise_title) LIKE '%barbell%'
            THEN REPLACE(exercise_title, '(Barbell)', '(Smith Machine)')
            ELSE exercise_title
        END                                             AS exercise_title_corrected

    FROM {{ ref('stg_sets') }}
    WHERE weight_kg > 0
    AND gym_detail IS NOT NULL
),

best_sets AS (
    SELECT
        d.workout_date,
        d.exercise_title_corrected                      AS exercise_title,
        d.gym,
        d.gym_detail,
        d.week_start,
        d.year,
        d.week_number,
        d.exercise_notes,
        d.equipment_mismatch,
        et.primary_muscle_group                         AS primary_muscle,
        et.equipment_category,
        CASE
            WHEN LOWER(et.equipment_category) = 'dumbbell'         THEN 'unilateral'
            WHEN LOWER(d.exercise_title) LIKE '%single arm%'       THEN 'unilateral'
            WHEN LOWER(d.exercise_title) LIKE '%single leg%'       THEN 'unilateral'
            ELSE 'bilateral'
        END                                             AS movement_type,
        MAX(d.weight_kg)                                AS max_weight_kg,
        MAX(CASE WHEN d.weight_kg = d.day_max_weight
                 THEN d.reps END)                       AS reps_at_max_weight,
        MAX(d.tonnage)                                  AS max_tonnage,
        COUNT(*)                                        AS total_sets
    FROM daily_sets d
    LEFT JOIN {{ source('bronze', 'dim_exercise_templates') }} et
        ON LOWER(d.exercise_title) = LOWER(et.title)
    GROUP BY
        d.workout_date, d.exercise_title_corrected, d.gym, d.gym_detail,
        d.week_start, d.year, d.week_number, d.exercise_notes,
        d.equipment_mismatch, et.primary_muscle_group, et.equipment_category, D.EXERCISE_TITLE
),

with_e1rm AS (
    SELECT
        *,
        CASE
            WHEN reps_at_max_weight = 1   THEN max_weight_kg
            WHEN reps_at_max_weight <= 10 THEN ROUND(max_weight_kg * (1 + reps_at_max_weight / 30.0), 2)
            WHEN reps_at_max_weight <= 20 THEN ROUND(max_weight_kg * (1 + reps_at_max_weight / 36.0), 2)
            ELSE NULL
        END                                             AS estimated_1rm
    FROM best_sets
),

with_history AS (
    SELECT
        *,
        COUNT(DISTINCT workout_date) OVER (
            PARTITION BY exercise_title, gym_detail
        )                                               AS total_sessions_logged,

        MAX(estimated_1rm) OVER (
            PARTITION BY exercise_title, gym_detail
            ORDER BY workout_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                               AS cumulative_pr_e1rm,

        AVG(estimated_1rm) OVER (
            PARTITION BY exercise_title, gym_detail
            ORDER BY workout_date
            ROWS BETWEEN 28 PRECEDING AND 22 PRECEDING
        )                                               AS e1rm_4wk_ago,

        AVG(estimated_1rm) OVER (
            PARTITION BY exercise_title, gym_detail
            ORDER BY workout_date
            ROWS BETWEEN 27 PRECEDING AND CURRENT ROW
        )                                               AS e1rm_rolling_4wk_avg
    FROM with_e1rm
    WHERE estimated_1rm IS NOT NULL
),

with_trend AS (
    SELECT
        *,
        ROUND(
            (estimated_1rm - e1rm_4wk_ago) /
            NULLIF(e1rm_4wk_ago, 0) * 100
        , 1)                                            AS e1rm_pct_change_4wk,

        (estimated_1rm = cumulative_pr_e1rm)            AS is_pr,

        DATEDIFF(
            'day',
            MAX(CASE WHEN estimated_1rm = cumulative_pr_e1rm
                     THEN workout_date END) OVER (
                PARTITION BY exercise_title, gym_detail
            ),
            CURRENT_DATE
        ) / 7                                           AS weeks_since_pr
    FROM with_history
),

with_readiness AS (
    SELECT
        t.*,
        f.training_status,
        (f.training_status = 'undertraining')           AS is_deload_week
    FROM with_trend t
    LEFT JOIN {{ ref('fatigue_index') }} f
        ON t.workout_date = f.workout_date
        AND t.gym_detail  = f.gym_detail
)

SELECT
    workout_date,
    week_start,
    year,
    week_number,
    exercise_title,
    primary_muscle,
    equipment_category,
    movement_type,
    gym,
    gym_detail,
    max_weight_kg,
    reps_at_max_weight,
    max_tonnage,
    total_sets,
    equipment_mismatch,
    estimated_1rm,
    cumulative_pr_e1rm,
    is_pr,
    e1rm_4wk_ago,
    e1rm_pct_change_4wk,
    e1rm_rolling_4wk_avg,
    weeks_since_pr,
    is_deload_week,
    training_status,
    total_sessions_logged,

    CASE
        WHEN total_sessions_logged < 4          THEN 'insufficient_data'
        WHEN is_deload_week                     THEN 'deload'
        WHEN e1rm_4wk_ago IS NULL               THEN 'insufficient_data'
        WHEN e1rm_pct_change_4wk >= 2.0         THEN 'improving'
        WHEN e1rm_pct_change_4wk <= -2.0        THEN 'declining'
        ELSE 'plateauing'
    END                                                 AS progression_trend,

    CASE
        WHEN e1rm_pct_change_4wk > 0 AND e1rm_4wk_ago IS NOT NULL
        THEN ROUND(
            LN((cumulative_pr_e1rm + 2.5) / NULLIF(estimated_1rm, 0)) /
            LN(1 + (e1rm_pct_change_4wk / 100) / 4)
        , 0)
        ELSE NULL
    END                                                 AS est_weeks_to_next_pr

FROM with_readiness