-- models/silver/stg_bodyweight_daily.sql
-- Daily bodyweight, linearly interpolated between readings.
--
-- There are only ~39 readings across 3.5 years, so any metric expressed
-- relative to bodyweight needs a value for days without a reading. Linear
-- interpolation between the surrounding readings is used; before the first
-- and after the last reading the nearest known value is carried flat.
--
-- body_fat_pct is deliberately not carried through: it is empty in every row.

{{ config(materialized = 'table') }}

WITH bounds AS (
    SELECT
        MIN(workout_date) AS min_date,
        MAX(workout_date) AS max_date
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

readings AS (
    SELECT measurement_date, weight_kg
    FROM {{ ref('stg_measurements') }}
    WHERE weight_kg IS NOT NULL
),

joined AS (
    SELECT
        sp.calendar_date,
        r.weight_kg AS reading_kg
    FROM spine sp
    LEFT JOIN readings r
        ON r.measurement_date = sp.calendar_date
),

-- locate the surrounding readings for every day
bracketed AS (
    SELECT
        calendar_date,
        reading_kg,
        LAST_VALUE(reading_kg IGNORE NULLS) OVER (
            ORDER BY calendar_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS prev_kg,
        LAST_VALUE(CASE WHEN reading_kg IS NOT NULL THEN calendar_date END IGNORE NULLS) OVER (
            ORDER BY calendar_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS prev_date,
        FIRST_VALUE(reading_kg IGNORE NULLS) OVER (
            ORDER BY calendar_date
            ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
        ) AS next_kg,
        FIRST_VALUE(CASE WHEN reading_kg IS NOT NULL THEN calendar_date END IGNORE NULLS) OVER (
            ORDER BY calendar_date
            ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
        ) AS next_date
    FROM joined
)

SELECT
    calendar_date                                       AS workout_date,
    ROUND(
        CASE
            WHEN reading_kg IS NOT NULL THEN reading_kg
            -- before the first / after the last reading: carry flat
            WHEN prev_kg IS NULL THEN next_kg
            WHEN next_kg IS NULL THEN prev_kg
            WHEN next_date = prev_date THEN prev_kg
            -- linear interpolation between the two surrounding readings
            ELSE prev_kg
                 + (next_kg - prev_kg)
                 * DATEDIFF('day', prev_date, calendar_date)
                 / NULLIF(DATEDIFF('day', prev_date, next_date), 0)
        END
    , 2)                                                AS bodyweight_kg,
    (reading_kg IS NOT NULL)                            AS is_measured,
    prev_date                                           AS prev_reading_date,
    next_date                                           AS next_reading_date
FROM bracketed
