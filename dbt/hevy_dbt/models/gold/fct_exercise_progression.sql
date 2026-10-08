-- models/gold/fct_exercise_progression.sql
-- GRAIN: one row per (exercise_title, gym_detail, workout_date).
-- Answers "am I getting stronger?"
--
-- Gym IS in the grain here, unlike the volume models: a machine's load is not
-- comparable between gyms, so a PR only means something within one venue.
-- is_cross_gym_comparable marks the free-weight movements where it does.
--
-- All trend windows use RANGE on workout_date, not ROWS. The previous version
-- used ROWS BETWEEN 28 PRECEDING AND 22 PRECEDING to mean "4 weeks ago",
-- which is 28 SESSIONS ago — for a once-a-week exercise, roughly 7 months.

{{ config(materialized = 'table') }}

WITH sets AS (
    SELECT
        s.*,
        -- these get logged as barbell but performed on a smith machine;
        -- keeping them under one title would mix two different movements
        CASE
            WHEN LOWER(s.exercise_notes) LIKE '%smith%'
             AND LOWER(s.exercise_title) LIKE '%barbell%'
            THEN REPLACE(s.exercise_title, '(Barbell)', '(Smith Machine)')
            ELSE s.exercise_title
        END AS exercise_title_resolved,
        (LOWER(s.exercise_notes) LIKE '%smith%'
         AND LOWER(s.exercise_title) LIKE '%barbell%') AS equipment_mismatch
    FROM {{ ref('stg_sets') }} s
    WHERE s.workout_date IS NOT NULL
      AND s.weight_kg > 0
      AND s.gym_detail IS NOT NULL
),

-- the heaviest set of the day for each exercise, and the best reps at it
top_set AS (
    SELECT
        workout_date,
        week_start,
        year,
        exercise_title_resolved                 AS exercise_title,
        gym,
        gym_detail,
        primary_muscle,
        equipment_category,
        MAX(equipment_mismatch)                 AS equipment_mismatch,
        MAX(weight_kg)                          AS top_set_weight_kg,
        MAX_BY(reps, weight_kg)                 AS top_set_reps,
        COUNT(*)                                AS sets_n,
        SUM(tonnage)                            AS exercise_tonnage,
        CASE
            WHEN LOWER(equipment_category) = 'dumbbell'    THEN 'unilateral'
            WHEN LOWER(exercise_title_resolved) LIKE '%single arm%' THEN 'unilateral'
            WHEN LOWER(exercise_title_resolved) LIKE '%single leg%' THEN 'unilateral'
            ELSE 'bilateral'
        END                                     AS movement_type
    FROM sets
    GROUP BY
        workout_date, week_start, year, exercise_title_resolved,
        gym, gym_detail, primary_muscle, equipment_category
),

with_e1rm AS (
    SELECT
        *,
        -- Epley below 10 reps, a flatter coefficient above it
        CASE
            WHEN top_set_reps = 1   THEN top_set_weight_kg
            WHEN top_set_reps <= 10 THEN ROUND(top_set_weight_kg * (1 + top_set_reps / 30.0), 2)
            WHEN top_set_reps <= 20 THEN ROUND(top_set_weight_kg * (1 + top_set_reps / 36.0), 2)
            ELSE NULL
        END                                     AS estimated_1rm
    FROM top_set
),

with_history AS (
    SELECT
        e.*,
        COUNT(*) OVER (PARTITION BY exercise_title, gym_detail)      AS sessions_logged,
        MAX(estimated_1rm) OVER (
            PARTITION BY exercise_title, gym_detail ORDER BY workout_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                                            AS best_1rm_to_date,
        -- genuinely ~4 weeks back in calendar time
        AVG(estimated_1rm) OVER (
            PARTITION BY exercise_title, gym_detail ORDER BY workout_date
            RANGE BETWEEN INTERVAL '35 days' PRECEDING AND INTERVAL '21 days' PRECEDING
        )                                                            AS e1rm_4wk_ago,
        AVG(estimated_1rm) OVER (
            PARTITION BY exercise_title, gym_detail ORDER BY workout_date
            RANGE BETWEEN INTERVAL '27 days' PRECEDING AND CURRENT ROW
        )                                                            AS e1rm_4wk_avg
    FROM with_e1rm e
    WHERE estimated_1rm IS NOT NULL
),

with_trend AS (
    SELECT
        h.*,
        (estimated_1rm >= best_1rm_to_date)     AS is_pr,
        ROUND(
            (estimated_1rm - e1rm_4wk_ago) / NULLIF(e1rm_4wk_ago, 0) * 100
        , 1)                                    AS e1rm_pct_change_4wk,
        DATEDIFF('day',
            MAX(CASE WHEN estimated_1rm >= best_1rm_to_date THEN workout_date END) OVER (
                PARTITION BY exercise_title, gym_detail ORDER BY workout_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ),
            workout_date
        )                                       AS days_since_pr
    FROM with_history h
)

SELECT
    t.workout_date,
    t.week_start,
    t.year,
    t.exercise_title,
    t.primary_muscle,
    t.equipment_category,
    t.movement_type,
    t.gym,
    t.gym_detail,
    t.sets_n,
    t.top_set_weight_kg,
    t.top_set_reps,
    t.exercise_tonnage,
    t.estimated_1rm,
    t.best_1rm_to_date,
    t.is_pr,
    t.days_since_pr,
    t.sessions_logged,
    ROUND(t.e1rm_4wk_ago, 2)                    AS e1rm_4wk_ago,
    ROUND(t.e1rm_4wk_avg, 2)                    AS e1rm_4wk_avg,
    t.e1rm_pct_change_4wk,
    t.equipment_mismatch,
    bw.bodyweight_kg,
    -- strength relative to bodyweight, now that bodyweight is interpolated
    ROUND(t.estimated_1rm / NULLIF(bw.bodyweight_kg, 0), 2) AS e1rm_per_kg_bodyweight,
    -- free weights transfer between gyms; machines do not
    (LOWER(t.equipment_category) IN ('barbell', 'dumbbell', 'kettlebell'))
                                                AS is_cross_gym_comparable,
    CASE
        WHEN t.sessions_logged < 3            THEN 'insufficient_data'
        WHEN t.e1rm_4wk_ago IS NULL           THEN 'insufficient_data'
        WHEN t.e1rm_pct_change_4wk >  2.5     THEN 'improving'
        WHEN t.e1rm_pct_change_4wk < -2.5     THEN 'declining'
        ELSE 'plateauing'
    END                                         AS progression_trend
FROM with_trend t
LEFT JOIN {{ ref('stg_bodyweight_daily') }} bw
    ON bw.workout_date = t.workout_date
