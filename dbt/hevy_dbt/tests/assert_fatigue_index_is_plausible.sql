-- An acute:chronic ratio outside 0-5 means the window or the zero-fill is
-- broken. Catches a regression to a ROWS frame or a missing date spine.
SELECT workout_date, fatigue_index
FROM {{ ref('fct_training_day') }}
WHERE fatigue_index < 0 OR fatigue_index > 5
