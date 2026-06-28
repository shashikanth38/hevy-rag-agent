-- models/silver/stg_sets.sql
{{
    config(
        materialized     = 'incremental',
        unique_key       = ['workout_id', 'exercise_title', 'set_index'],
        on_schema_change = 'sync_all_columns'
    )
}}

WITH raw AS (
    SELECT * FROM {{ source('bronze', 'raw_workouts') }}
    WHERE set_type IN ('normal', 'failure')

    {% if is_incremental() %}
        AND _loaded_at > (SELECT MAX(_loaded_at) FROM {{ this }})
    {% endif %}
),

cleaned AS (
    SELECT
        -- identity
        workout_id,
        title                                                       AS workout_title,
        exercise_title,
        COALESCE(superset_id, '')                                   AS superset_id,
        TRY_CAST(set_index AS INTEGER)                              AS set_index,
        LOWER(set_type)                                             AS set_type,

        -- timestamps — handle both CSV and API formats
        COALESCE(
            TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(start_time)
        )                                                           AS start_time,

        COALESCE(
            TRY_TO_TIMESTAMP(end_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(end_time)
        )                                                           AS end_time,

        DATE(COALESCE(
            TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(start_time)
        ))                                                          AS workout_date,

        -- time dimensions
        EXTRACT(year FROM COALESCE(
            TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(start_time)
        ))                                                          AS year,

        EXTRACT(month FROM COALESCE(
            TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(start_time)
        ))                                                          AS month,

        EXTRACT(week FROM COALESCE(
            TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(start_time)
        ))                                                          AS week_number,

        DATE_TRUNC('week', COALESCE(
            TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(start_time)
        ))                                                          AS week_start,

        -- metrics
        COALESCE(TRY_TO_DECIMAL(weight_kg, 6, 2), 0.0)             AS weight_kg,
        COALESCE(TRY_TO_NUMBER(reps), 0)                            AS reps,
        TRY_TO_DECIMAL(rpe, 4, 1)                                   AS rpe,

        -- derived
        COALESCE(TRY_TO_DECIMAL(weight_kg, 6, 2), 0.0)
            * COALESCE(TRY_TO_NUMBER(reps), 0)                      AS tonnage,

        DATEDIFF('minute',
            COALESCE(
                TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),
                TRY_TO_TIMESTAMP_NTZ(start_time)
            ),
            COALESCE(
                TRY_TO_TIMESTAMP(end_time, 'MON DD, YYYY, HH12:MI PM'),
                TRY_TO_TIMESTAMP_NTZ(end_time)
            )
        )                                                           AS session_duration_min,

        (COALESCE(superset_id, '') != '')                           AS is_superset,
        (LOWER(set_type) = 'failure')                               AS is_failure_set,

        -- gym detection from description
        COALESCE(
            CASE
                WHEN LOWER(description)    LIKE '%thrive%'          THEN 'thrive'
                WHEN LOWER(description)    LIKE '%cult chennai%'    THEN 'cult_chennai'
                WHEN LOWER(description)    LIKE '%afterburn%'       THEN 'cult_afterburn'
                WHEN LOWER(description)    LIKE '%vr fitness%'      THEN 'vr_fitness'
                WHEN LOWER(description)    LIKE '%blr%'             THEN 'blr_other'
                WHEN LOWER(description)    LIKE '%pune%'            THEN 'pune_other'
                ELSE NULL
            END,
            CASE
                WHEN LOWER(exercise_notes) LIKE '%thrive%'          THEN 'thrive'
                WHEN LOWER(exercise_notes) LIKE '%cult chennai%'    THEN 'cult_chennai'
                WHEN LOWER(exercise_notes) LIKE '%afterburn%'       THEN 'cult_afterburn'
                WHEN LOWER(exercise_notes) LIKE '%vr fitness%'      THEN 'vr_fitness'
                WHEN LOWER(exercise_notes) LIKE '%blr%'             THEN 'blr_other'
                WHEN LOWER(exercise_notes) LIKE '%pune%'            THEN 'pune_other'
                ELSE NULL
            END
        )                                                           AS gym_detail_from_description,

        CASE
            WHEN LOWER(description)        LIKE '%thrive%'          THEN 'chennai'
            WHEN LOWER(description)        LIKE '%cult chennai%'    THEN 'chennai'
            WHEN LOWER(description)        LIKE '%afterburn%'       THEN 'hyd'
            WHEN LOWER(description)        LIKE '%vr fitness%'      THEN 'hyd'
            WHEN LOWER(description)        LIKE '%blr%'             THEN 'blr_other'
            WHEN LOWER(description)        LIKE '%pune%'            THEN 'pune_other'
            ELSE NULL
        END                                                         AS gym_from_description,

        description,
        exercise_notes,
        _source,
        _loaded_at

    FROM raw
),

joined AS (
    SELECT
        c.*,
        gl.gym        AS gl_gym,
        gl.gym_detail AS gl_gym_detail
    FROM cleaned c
    LEFT JOIN {{ source('bronze', 'gym_locations') }} gl
        ON c.workout_date BETWEEN gl.start_date AND gl.end_date
)

SELECT
    workout_id,
    workout_title,
    exercise_title,
    superset_id,
    set_index,
    set_type,
    start_time,
    end_time,
    workout_date,
    year,
    month,
    week_number,
    week_start,
    weight_kg,
    reps,
    rpe,
    tonnage,
    session_duration_min,
    is_superset,
    is_failure_set,
    COALESCE(gym_detail_from_description, gl_gym_detail)     AS gym_detail,
    COALESCE(gym_from_description, gl_gym)                   AS gym,
    (gl_gym IS NOT NULL OR gym_from_description IS NOT NULL) AS is_gym_known,
    _source,
    _loaded_at
FROM joined