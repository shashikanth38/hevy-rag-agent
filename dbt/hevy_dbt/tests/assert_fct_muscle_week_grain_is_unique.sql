SELECT week_start, muscle, COUNT(*) AS n
FROM {{ ref('fct_muscle_week') }}
GROUP BY 1, 2
HAVING COUNT(*) > 1
