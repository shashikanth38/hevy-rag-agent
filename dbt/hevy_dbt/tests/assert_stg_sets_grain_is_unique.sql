-- The declared unique_key must actually be unique. This is the test that
-- would have caught the 95 duplicate-key rows: dbt's incremental merge
-- cannot dedupe within a single incoming batch, so re-sync overlap slipped
-- straight through unnoticed.
SELECT workout_id, exercise_title, set_index, set_occurrence, COUNT(*) AS n
FROM {{ ref('stg_sets') }}
GROUP BY 1, 2, 3, 4
HAVING COUNT(*) > 1
