SELECT exercise_title, gym_detail, workout_date, COUNT(*) AS n
FROM {{ ref('fct_exercise_progression') }}
GROUP BY 1, 2, 3
HAVING COUNT(*) > 1
