-- models/gold/fct_muscle_week.sql
-- GRAIN: one row per (week_start, muscle). Answers "am I doing enough for X?"
--
-- Replaces weekly_volume + muscle_balance. Those stored one COLUMN per
-- muscle (chest_sets, chest_below_mev, chest_neglected, ...), which meant 24
-- hardcoded booleans covering only 8 of 20 trained muscles — calves,
-- abdominals, traps, forearms and glutes were invisible. Here muscles are
-- ROWS and the thresholds come from the muscle_landmarks seed, so every
-- muscle is covered and adding one is a CSV edit, not a SQL rewrite.
--
-- Every (week, muscle) pair exists, including untrained ones, so a neglected
-- muscle is a row with direct_sets = 0 rather than an absent row.
--
-- Gym is deliberately NOT in the grain. Volume is systemic like fatigue, and
-- splitting by gym both fragmented the data and broke the week-over-week
-- comparison. Gym belongs to fct_exercise_progression, where PRs need it.
--
-- effective_sets gives secondary muscles half credit, the usual convention:
-- a close-grip bench is real triceps volume, just not as much as a pushdown.
-- secondary_muscle_groups was previously unused.

{{ config(materialized = 'table') }}

WITH sets AS (
    SELECT * FROM {{ ref('stg_sets') }}
    WHERE workout_date IS NOT NULL
),

weeks AS (
    SELECT DISTINCT week_start FROM sets
),

landmarks AS (
    SELECT muscle, movement_group, mev_sets, mav_sets, mrv_sets
    FROM {{ ref('muscle_landmarks') }}
),

-- every muscle in every week, so untrained weeks are visible
grid AS (
    SELECT
        w.week_start,
        l.muscle,
        l.movement_group,
        l.mev_sets,
        l.mav_sets,
        l.mrv_sets
    FROM weeks w
    CROSS JOIN landmarks l
),

direct AS (
    SELECT
        week_start,
        primary_muscle                  AS muscle,
        COUNT(*)                        AS direct_sets,
        SUM(reps)                       AS total_reps,
        SUM(tonnage)                    AS total_tonnage,
        AVG(weight_kg)                  AS avg_weight_kg,
        MAX(weight_kg)                  AS max_weight_kg,
        COUNT(DISTINCT exercise_title)  AS unique_exercises,
        COUNT(DISTINCT workout_date)    AS training_days,
        SUM(CASE WHEN is_failure_set THEN 1 ELSE 0 END) AS failure_sets,
        SUM(CASE WHEN is_unloaded_set THEN 1 ELSE 0 END) AS unloaded_sets
    FROM sets
    GROUP BY week_start, primary_muscle
),

-- secondary muscle involvement, parsed from the JSON array on the template
indirect AS (
    SELECT
        s.week_start,
        f.value::STRING                 AS muscle,
        COUNT(*)                        AS indirect_sets
    FROM sets s,
         LATERAL FLATTEN(input => TRY_PARSE_JSON(s.secondary_muscle_groups)) f
    GROUP BY s.week_start, f.value::STRING
),

combined AS (
    SELECT
        g.week_start,
        EXTRACT(year FROM g.week_start)             AS year,
        g.muscle,
        g.movement_group,
        g.mev_sets,
        g.mav_sets,
        g.mrv_sets,
        COALESCE(d.direct_sets,      0)             AS direct_sets,
        COALESCE(i.indirect_sets,    0)             AS indirect_sets,
        COALESCE(d.direct_sets, 0)
            + 0.5 * COALESCE(i.indirect_sets, 0)    AS effective_sets,
        COALESCE(d.total_reps,       0)             AS total_reps,
        COALESCE(d.total_tonnage,    0)             AS total_tonnage,
        d.avg_weight_kg,
        d.max_weight_kg,
        COALESCE(d.unique_exercises, 0)             AS unique_exercises,
        COALESCE(d.training_days,    0)             AS training_days,
        COALESCE(d.failure_sets,     0)             AS failure_sets,
        COALESCE(d.unloaded_sets,    0)             AS unloaded_sets
    FROM grid g
    LEFT JOIN direct   d ON d.week_start = g.week_start AND d.muscle = g.muscle
    LEFT JOIN indirect i ON i.week_start = g.week_start AND i.muscle = g.muscle
),

with_history AS (
    SELECT
        c.*,
        -- last week this muscle actually got direct work
        DATEDIFF('week',
            MAX(CASE WHEN direct_sets > 0 THEN week_start END) OVER (
                PARTITION BY muscle ORDER BY week_start
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ),
            week_start
        )                                           AS weeks_since_trained,
        AVG(direct_sets) OVER (
            PARTITION BY muscle ORDER BY week_start
            RANGE BETWEEN INTERVAL '27 days' PRECEDING AND CURRENT ROW
        )                                           AS sets_4wk_avg
    FROM combined c
)

SELECT
    h.week_start,
    h.year,
    h.muscle,
    h.movement_group,

    h.direct_sets,
    h.indirect_sets,
    ROUND(h.effective_sets, 1)                      AS effective_sets,
    h.total_reps,
    h.total_tonnage,
    ROUND(h.avg_weight_kg, 2)                       AS avg_weight_kg,
    h.max_weight_kg,
    h.unique_exercises,
    h.training_days,
    h.failure_sets,
    h.unloaded_sets,

    h.mev_sets,
    h.mav_sets,
    h.mrv_sets,
    h.weeks_since_trained,
    ROUND(h.sets_4wk_avg, 1)                        AS sets_4wk_avg,

    -- week-over-week against the preceding CALENDAR week; NULL when that
    -- week has no row rather than silently comparing across a gap
    p.direct_sets                                   AS prev_week_sets,
    p.total_tonnage                                 AS prev_week_tonnage,
    ROUND(
        (h.total_tonnage - p.total_tonnage)
        / NULLIF(p.total_tonnage, 0) * 100
    , 1)                                            AS tonnage_pct_change,

    CASE
        WHEN h.mev_sets IS NULL          THEN 'not_tracked'
        WHEN h.direct_sets = 0           THEN 'not_trained'
        WHEN h.direct_sets < h.mev_sets  THEN 'below_mev'
        WHEN h.direct_sets > h.mrv_sets  THEN 'above_mrv'
        WHEN h.direct_sets > h.mav_sets  THEN 'above_mav'
        ELSE 'optimal'
    END                                             AS volume_status
FROM with_history h
LEFT JOIN with_history p
    ON  p.week_start = DATEADD('day', -7, h.week_start)
    AND p.muscle     = h.muscle
