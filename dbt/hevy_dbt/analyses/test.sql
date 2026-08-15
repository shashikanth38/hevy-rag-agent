select * from {{source('bronze', 'raw_workouts')}}  limit 10


SELECT 
    workout_title,
    workout_date,
    start_time,
    COUNT(*) AS cnt
FROM {{ ref('stg_sessions') }}
GROUP BY workout_title, workout_date, start_time
HAVING COUNT(*) > 1
ORDER BY cnt DESC
LIMIT 10;

select distinct PRIMARY_MUSCLE from {{ ref('weekly_volume') }} limit 10


"quadriceps","biceps","abdominals","abductors","traps","glutes","chest","lats","calves","triceps"


"PRIMARY_MUSCLE","full_body","traps","glutes","biceps","forearms","hamstrings","chest","upper_back","shoulders","quadriceps"
