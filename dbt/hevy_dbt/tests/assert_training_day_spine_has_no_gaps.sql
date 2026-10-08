-- fct_training_day must cover every calendar day with no holes, otherwise
-- the RANGE windows silently under-count rest days.
WITH d AS (
    SELECT workout_date,
           LAG(workout_date) OVER (ORDER BY workout_date) AS prev_date
    FROM {{ ref('fct_training_day') }}
)
SELECT workout_date, prev_date, DATEDIFF('day', prev_date, workout_date) AS gap_days
FROM d
WHERE prev_date IS NOT NULL AND DATEDIFF('day', prev_date, workout_date) <> 1
