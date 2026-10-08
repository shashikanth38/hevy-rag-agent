-- models/gold/fct_training_day.sql
-- GRAIN: one row per calendar day. Answers "should I rest?"
--
-- Acute:chronic workload ratio (7 calendar days vs 28 calendar days):
--   > 1.2 = overreaching, < 0.8 = undertraining, else optimal
--
-- The grain is a CALENDAR DATE SPINE, not a list of training days. Rest days
-- must exist as daily_tonnage = 0 rows, otherwise a window frame counts
-- sessions instead of days: a ROWS BETWEEN 6 PRECEDING frame over session
-- rows spans ~2.3 calendar weeks at 3 sessions/week, and time off never
-- lowers the acute average. AVG over a RANGE frame also divides by rows
-- present, so the zero-fill fixes the denominator as well as the window.
--
-- Load is SYSTEMIC: tonnage sums across gyms. Fatigue belongs to the body,
-- not the venue, so there is no PARTITION BY gym. gym_detail is the dominant
-- gym of that day — context only, never a grouping key.
--
-- The spine ends at the last training day rather than CURRENT_DATE, so a
-- stale pipeline is not misreported as months of deliberate rest.

{{ config(materialized = 'table') }}

WITH bounds AS (
    SELECT MIN(workout_date) AS min_date, MAX(workout_date) AS max_date
    FROM {{ ref('stg_sets') }}
    WHERE workout_date IS NOT NULL
),

spine AS (
    SELECT calendar_date
    FROM (
        SELECT DATEADD('day', SEQ4(), (SELECT min_date FROM bounds)) AS calendar_date
        FROM TABLE(GENERATOR(ROWCOUNT => 20000))
    )
    WHERE calendar_date <= (SELECT max_date FROM bounds)
),

trained AS (
    SELECT
        workout_date,
        SUM(tonnage)                            AS daily_tonnage,
        COUNT(*)                                AS daily_sets,
        COUNT(DISTINCT exercise_title)          AS daily_exercises,
        COUNT(DISTINCT primary_muscle)          AS muscles_trained,
        COUNT(DISTINCT workout_id)              AS sessions,
        SUM(CASE WHEN is_failure_set THEN 1 ELSE 0 END) AS failure_sets,
        MAX(session_duration_min)               AS session_duration_min,
        MAX_BY(gym_detail, tonnage)             AS dominant_gym_detail,
        MAX_BY(gym,        tonnage)             AS dominant_gym,
        COUNT(DISTINCT gym_detail)              AS gyms_trained
    FROM {{ ref('stg_sets') }}
    WHERE workout_date IS NOT NULL
    GROUP BY workout_date
),

daily AS (
    SELECT
        sp.calendar_date                        AS workout_date,
        COALESCE(t.daily_tonnage,    0)         AS daily_tonnage,
        COALESCE(t.daily_sets,       0)         AS daily_sets,
        COALESCE(t.daily_exercises,  0)         AS daily_exercises,
        COALESCE(t.muscles_trained,  0)         AS muscles_trained,
        COALESCE(t.sessions,         0)         AS sessions,
        COALESCE(t.failure_sets,     0)         AS failure_sets,
        COALESCE(t.gyms_trained,     0)         AS gyms_trained,
        t.session_duration_min,
        t.dominant_gym_detail                   AS gym_detail,
        t.dominant_gym                          AS gym,
        (t.workout_date IS NOT NULL)            AS is_training_day
    FROM spine sp
    LEFT JOIN trained t ON t.workout_date = sp.calendar_date
),

with_rolling AS (
    SELECT
        d.*,
        AVG(daily_tonnage) OVER (
            ORDER BY workout_date
            RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
        )                                       AS rolling_7d_avg_tonnage,
        AVG(daily_tonnage) OVER (
            ORDER BY workout_date
            RANGE BETWEEN INTERVAL '27 days' PRECEDING AND CURRENT ROW
        )                                       AS rolling_28d_avg_tonnage,
        SUM(daily_sets) OVER (
            ORDER BY workout_date
            RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
        )                                       AS sets_last_7d,
        -- consecutive rest days ending today
        DATEDIFF('day',
            MAX(CASE WHEN is_training_day THEN workout_date END) OVER (
                ORDER BY workout_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ),
            workout_date
        )                                       AS days_since_last_session,
        DATEDIFF('day', (SELECT min_date FROM bounds), workout_date) + 1
                                                AS days_of_history
    FROM daily d
),

scored AS (
    SELECT
        w.*,
        bw.bodyweight_kg,
        rolling_7d_avg_tonnage
            / NULLIF(rolling_28d_avg_tonnage, 0) AS acwr_raw
    FROM with_rolling w
    LEFT JOIN {{ ref('stg_bodyweight_daily') }} bw
        ON bw.workout_date = w.workout_date
)

SELECT
    workout_date,
    is_training_day,
    gym,
    gym_detail,
    gyms_trained,
    sessions,
    daily_tonnage,
    daily_sets,
    daily_exercises,
    muscles_trained,
    failure_sets,
    session_duration_min,
    ROUND(daily_tonnage / NULLIF(session_duration_min, 0), 1) AS tonnage_per_min,
    sets_last_7d,
    days_since_last_session,
    days_of_history,
    bodyweight_kg,
    ROUND(rolling_7d_avg_tonnage,  2)       AS rolling_7d_avg_tonnage,
    ROUND(rolling_28d_avg_tonnage, 2)       AS rolling_28d_avg_tonnage,
    ROUND(COALESCE(acwr_raw, 0), 2)         AS fatigue_index,
    CASE
        -- the 28d window is not yet 28 days wide, so the ratio is biased
        -- toward 1.0 and must not be read as a real signal
        WHEN days_of_history < 28  THEN 'insufficient_data'
        WHEN acwr_raw IS NULL      THEN 'insufficient_data'
        WHEN acwr_raw > 1.2        THEN 'overreaching'
        WHEN acwr_raw < 0.8        THEN 'undertraining'
        ELSE 'optimal'
    END                                     AS training_status
FROM scored
