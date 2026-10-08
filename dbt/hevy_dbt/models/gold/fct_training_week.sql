-- models/gold/fct_training_week.sql
-- GRAIN: one row per week. Answers "is my split balanced?"
--
-- Thin rollup on top of fct_muscle_week. push/pull/leg come from the
-- movement_group column on the muscle_landmarks seed, so there are no
-- hardcoded muscle lists here: reclassifying a muscle is a CSV edit.

{{ config(materialized = 'table') }}

WITH by_group AS (
    SELECT
        week_start,
        year,
        SUM(CASE WHEN movement_group = 'push' THEN direct_sets ELSE 0 END) AS push_sets,
        SUM(CASE WHEN movement_group = 'pull' THEN direct_sets ELSE 0 END) AS pull_sets,
        SUM(CASE WHEN movement_group = 'legs' THEN direct_sets ELSE 0 END) AS leg_sets,
        SUM(CASE WHEN movement_group = 'core' THEN direct_sets ELSE 0 END) AS core_sets,
        SUM(direct_sets)                                        AS total_sets,
        SUM(total_tonnage)                                      AS total_tonnage,
        SUM(total_reps)                                         AS total_reps,
        MAX(training_days)                                      AS training_days,
        COUNT(CASE WHEN volume_status = 'below_mev'   THEN 1 END) AS muscles_below_mev,
        COUNT(CASE WHEN volume_status = 'not_trained' THEN 1 END) AS muscles_not_trained,
        COUNT(CASE WHEN volume_status = 'above_mrv'   THEN 1 END) AS muscles_above_mrv,
        -- the two muscles whose imbalance is a recognised injury signal
        SUM(CASE WHEN muscle = 'quadriceps' THEN direct_sets ELSE 0 END) AS quad_sets,
        SUM(CASE WHEN muscle = 'hamstrings' THEN direct_sets ELSE 0 END) AS hamstring_sets
    FROM {{ ref('fct_muscle_week') }}
    GROUP BY week_start, year
),

with_ratios AS (
    SELECT
        *,
        ROUND(push_sets / NULLIF(pull_sets, 0), 2)      AS push_pull_ratio,
        ROUND(quad_sets / NULLIF(hamstring_sets, 0), 2) AS quad_ham_ratio
    FROM by_group
)

SELECT
    w.week_start,
    w.year,
    w.training_days,
    w.total_sets,
    w.total_reps,
    w.total_tonnage,
    w.push_sets,
    w.pull_sets,
    w.leg_sets,
    w.core_sets,
    w.quad_sets,
    w.hamstring_sets,
    w.muscles_below_mev,
    w.muscles_not_trained,
    w.muscles_above_mrv,
    w.push_pull_ratio,
    w.quad_ham_ratio,
    p.total_tonnage                                     AS prev_week_tonnage,
    ROUND(
        (w.total_tonnage - p.total_tonnage)
        / NULLIF(p.total_tonnage, 0) * 100
    , 1)                                                AS tonnage_pct_change,
    CASE
        WHEN w.pull_sets = 0        THEN 'no_pull_training'
        WHEN w.push_pull_ratio > 1.3 THEN 'push_dominant'
        WHEN w.push_pull_ratio < 0.7 THEN 'pull_dominant'
        ELSE 'balanced'
    END                                                 AS push_pull_status,
    CASE
        WHEN w.hamstring_sets = 0    THEN 'no_hamstring_training'
        WHEN w.quad_ham_ratio > 2.0  THEN 'quad_dominant_injury_risk'
        WHEN w.quad_ham_ratio < 1.0  THEN 'hamstring_dominant'
        ELSE 'balanced'
    END                                                 AS quad_ham_status
FROM with_ratios w
LEFT JOIN with_ratios p
    ON p.week_start = DATEADD('day', -7, w.week_start)
