-- models/gold/muscle_balance.sql

{{
    config(
        materialized = 'table',
        on_schema_change = 'sync_all_columns'
    )
}}

WITH tracked_muscles AS (
    SELECT
        week_start,
        gym_detail,
        primary_muscle,
        total_sets,
        total_tonnage,
        training_days
    FROM {{ ref('weekly_volume') }}
    WHERE primary_muscle IN (
        'shoulders', 'biceps', 'triceps',
        'quadriceps', 'hamstrings',
        'chest', 'lats', 'upper_back'
    )
    AND gym_detail IS NOT NULL
),

-- weekly sets per muscle per gym
weekly_by_muscle AS (
    SELECT
        week_start,
        gym_detail,
        SUM(CASE WHEN primary_muscle = 'chest'      THEN total_sets ELSE 0 END) AS chest_sets,
        SUM(CASE WHEN primary_muscle = 'shoulders'  THEN total_sets ELSE 0 END) AS shoulder_sets,
        SUM(CASE WHEN primary_muscle = 'triceps'    THEN total_sets ELSE 0 END) AS tricep_sets,
        SUM(CASE WHEN primary_muscle = 'biceps'     THEN total_sets ELSE 0 END) AS bicep_sets,
        SUM(CASE WHEN primary_muscle = 'lats'       THEN total_sets ELSE 0 END) AS lat_sets,
        SUM(CASE WHEN primary_muscle = 'upper_back' THEN total_sets ELSE 0 END) AS upper_back_sets,
        SUM(CASE WHEN primary_muscle = 'quadriceps' THEN total_sets ELSE 0 END) AS quad_sets,
        SUM(CASE WHEN primary_muscle = 'hamstrings' THEN total_sets ELSE 0 END) AS hamstring_sets,

        -- push = chest + shoulders + triceps
        SUM(CASE WHEN primary_muscle IN ('chest','shoulders','triceps')
                 THEN total_sets ELSE 0 END)                        AS push_sets,

        -- pull = lats + upper_back + biceps
        SUM(CASE WHEN primary_muscle IN ('lats','upper_back','biceps')
                 THEN total_sets ELSE 0 END)                        AS pull_sets,

        -- legs = quads + hamstrings
        SUM(CASE WHEN primary_muscle IN ('quadriceps','hamstrings')
                 THEN total_sets ELSE 0 END)                        AS leg_sets,

        -- total tracked sets this week
        SUM(total_sets)                                             AS total_tracked_sets
    FROM tracked_muscles
    GROUP BY week_start, gym_detail
),

-- ratios + balance signals
with_ratios AS (
    SELECT
        *,
        -- push/pull ratio (ideal ~1.0, >1.2 = chest/shoulder dominant)
        ROUND(
            push_sets / NULLIF(pull_sets, 0)
        , 2)                                                        AS push_pull_ratio,

        -- quad/hamstring ratio (ideal ~1.5, >2.0 = injury risk)
        ROUND(
            quad_sets / NULLIF(hamstring_sets, 0)
        , 2)                                                        AS quad_ham_ratio,

        -- bicep/tricep ratio (ideal ~1.0)
        ROUND(
            bicep_sets / NULLIF(tricep_sets, 0)
        , 2)                                                        AS bicep_tricep_ratio,

        -- lat/upper_back ratio (ideal ~1.0-1.5)
        ROUND(
            lat_sets / NULLIF(upper_back_sets, 0)
        , 2)                                                        AS lat_upper_back_ratio
    FROM weekly_by_muscle
    WHERE total_tracked_sets > 0
),

-- personal baseline (26 week rolling avg per gym)
with_baseline AS (
    SELECT
        *,
        AVG(push_pull_ratio) OVER (
            PARTITION BY gym_detail
            ORDER BY week_start
            ROWS BETWEEN 25 PRECEDING AND CURRENT ROW
        )                                                           AS personal_avg_push_pull,

        AVG(quad_ham_ratio) OVER (
            PARTITION BY gym_detail
            ORDER BY week_start
            ROWS BETWEEN 25 PRECEDING AND CURRENT ROW
        )                                                           AS personal_avg_quad_ham
    FROM with_ratios
),

-- MAV thresholds from Israetel (2021)
with_mav AS (
    SELECT
        *,
        -- flag muscles below minimum effective volume (MEV)
        CASE WHEN chest_sets      < 8  THEN TRUE ELSE FALSE END     AS chest_below_mev,
        CASE WHEN shoulder_sets   < 8  THEN TRUE ELSE FALSE END     AS shoulders_below_mev,
        CASE WHEN tricep_sets     < 6  THEN TRUE ELSE FALSE END     AS triceps_below_mev,
        CASE WHEN bicep_sets      < 6  THEN TRUE ELSE FALSE END     AS biceps_below_mev,
        CASE WHEN lat_sets        < 8  THEN TRUE ELSE FALSE END     AS lats_below_mev,
        CASE WHEN upper_back_sets < 8  THEN TRUE ELSE FALSE END     AS upper_back_below_mev,
        CASE WHEN quad_sets       < 8  THEN TRUE ELSE FALSE END     AS quads_below_mev,
        CASE WHEN hamstring_sets  < 6  THEN TRUE ELSE FALSE END     AS hamstrings_below_mev,

        -- flag muscles above maximum adaptive volume (MAV)
        CASE WHEN chest_sets      > 18 THEN TRUE ELSE FALSE END     AS chest_above_mav,
        CASE WHEN shoulder_sets   > 20 THEN TRUE ELSE FALSE END     AS shoulders_above_mav,
        CASE WHEN tricep_sets     > 14 THEN TRUE ELSE FALSE END     AS triceps_above_mav,
        CASE WHEN bicep_sets      > 14 THEN TRUE ELSE FALSE END     AS biceps_above_mav,
        CASE WHEN lat_sets        > 16 THEN TRUE ELSE FALSE END     AS lats_above_mav,
        CASE WHEN upper_back_sets > 16 THEN TRUE ELSE FALSE END     AS upper_back_above_mav,
        CASE WHEN quad_sets       > 20 THEN TRUE ELSE FALSE END     AS quads_above_mav,
        CASE WHEN hamstring_sets  > 16 THEN TRUE ELSE FALSE END     AS hamstrings_above_mav
    FROM with_baseline
)

SELECT
    week_start,
    gym_detail,

    -- individual muscle sets
    chest_sets,
    shoulder_sets,
    tricep_sets,
    bicep_sets,
    lat_sets,
    upper_back_sets,
    quad_sets,
    hamstring_sets,

    -- category totals
    push_sets,
    pull_sets,
    leg_sets,
    total_tracked_sets,

    -- ratios
    push_pull_ratio,
    quad_ham_ratio,
    bicep_tricep_ratio,
    lat_upper_back_ratio,

    -- personal baselines
    ROUND(personal_avg_push_pull, 2)                                AS personal_avg_push_pull,
    ROUND(personal_avg_quad_ham,  2)                                AS personal_avg_quad_ham,

    -- balance alerts
    CASE
        WHEN push_pull_ratio > 1.3
            THEN 'push_dominant'
        WHEN push_pull_ratio < 0.7
            THEN 'pull_dominant'
        ELSE 'balanced'
    END                                                             AS push_pull_status,

CASE
    WHEN quad_ham_ratio IS NULL     THEN 'no_leg_training'
    WHEN quad_ham_ratio > 2.0       THEN 'quad_dominant — injury risk'
    WHEN quad_ham_ratio < 1.0       THEN 'hamstring_dominant'
    ELSE 'balanced'
END AS quad_ham_status,

    -- MEV flags
    chest_below_mev,
    shoulders_below_mev,
    triceps_below_mev,
    biceps_below_mev,
    lats_below_mev,
    upper_back_below_mev,
    quads_below_mev,
    hamstrings_below_mev,

    -- MAV flags
    chest_above_mav,
    shoulders_above_mav,
    triceps_above_mav,
    biceps_above_mav,
    lats_above_mav,
    upper_back_above_mav,
    quads_above_mav,
    hamstrings_above_mav,

    -- deviation from personal baseline
    ROUND(push_pull_ratio - personal_avg_push_pull, 2)             AS push_pull_vs_baseline,

    -- neglect flag — no sets this week for a tracked muscle
    CASE WHEN chest_sets      = 0 THEN TRUE ELSE FALSE END          AS chest_neglected,
    CASE WHEN shoulder_sets   = 0 THEN TRUE ELSE FALSE END          AS shoulders_neglected,
    CASE WHEN tricep_sets     = 0 THEN TRUE ELSE FALSE END          AS triceps_neglected,
    CASE WHEN bicep_sets      = 0 THEN TRUE ELSE FALSE END          AS biceps_neglected,
    CASE WHEN lat_sets        = 0 THEN TRUE ELSE FALSE END          AS lats_neglected,
    CASE WHEN upper_back_sets = 0 THEN TRUE ELSE FALSE END          AS upper_back_neglected,
    CASE WHEN quad_sets       = 0 THEN TRUE ELSE FALSE END          AS quads_neglected,
    CASE WHEN hamstring_sets  = 0 THEN TRUE ELSE FALSE END          AS hamstrings_neglected

FROM with_mav
ORDER BY week_start DESC, gym_detail