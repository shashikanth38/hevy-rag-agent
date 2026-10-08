-- models/silver/stg_sets.sql
-- One row per working set. The single source of truth for every gold model.
--
-- Working sets = normal + failure + dropset. Warmups are excluded.
-- Dropsets ARE working volume; excluding them discarded ~540 real sets.
--
-- GRAIN: [workout_id, exercise_title, set_index, set_occurrence]
-- set_occurrence exists because the natural key collides. Doing the same
-- exercise twice in one session gives both blocks set_index 0,1,2..., and
-- the CSV surrogate workout_id (MD5 of title + start_time) cannot separate
-- them. Exact duplicate rows are collapsed first (re-sync overlap produced
-- 76 byte-identical copies); genuinely different sets that share a key are
-- then numbered, so nothing is lost and the key is honest.
--
-- The exercise dimension is joined HERE, not in gold, so muscle and
-- equipment are resolved once and every gold model agrees.

{{
    config(
        materialized     = 'incremental',
        unique_key       = ['workout_id', 'exercise_title', 'set_index', 'set_occurrence'],
        on_schema_change = 'sync_all_columns'
    )
}}

WITH raw AS (
    SELECT * FROM {{ source('bronze', 'raw_workouts') }}
    WHERE set_type IN ('normal', 'failure', 'dropset')

    {% if is_incremental() %}
        AND _loaded_at > (SELECT MAX(_loaded_at) FROM {{ this }})
    {% endif %}
),

-- collapse rows that are byte-identical on the full set definition:
-- these are re-sync artifacts, not real repeated work
deduped AS (
    SELECT *
    FROM raw
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY workout_id, exercise_title, set_index,
                     set_type, weight_kg, reps
        ORDER BY _loaded_at DESC
    ) = 1
),

-- number what remains: a repeated key here means the exercise genuinely
-- appeared more than once in the session
keyed AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY workout_id, exercise_title, set_index
            ORDER BY _loaded_at, weight_kg, reps
        ) AS set_occurrence
    FROM deduped
),

-- parse the two timestamp formats exactly once
parsed AS (
    SELECT
        k.*,
        COALESCE(
            TRY_TO_TIMESTAMP(k.start_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(k.start_time)
        ) AS start_ts,
        COALESCE(
            TRY_TO_TIMESTAMP(k.end_time, 'MON DD, YYYY, HH12:MI PM'),
            TRY_TO_TIMESTAMP_NTZ(k.end_time)
        ) AS end_ts,
        -- description and notes both carry gym tags; scan them as one field
        LOWER(COALESCE(k.description, '') || ' ' || COALESCE(k.exercise_notes, '')) AS tag_text
    FROM keyed k
),

typed AS (
    SELECT
        workout_id,
        title                                       AS workout_title,
        exercise_title,
        set_index,
        set_occurrence,
        LOWER(set_type)                             AS set_type,
        COALESCE(superset_id, '')                   AS superset_id,

        start_ts                                    AS start_time,
        end_ts                                      AS end_time,
        DATE(start_ts)                              AS workout_date,
        DATE_TRUNC('week', start_ts)::DATE          AS week_start,
        EXTRACT(year  FROM start_ts)                AS year,
        EXTRACT(month FROM start_ts)                AS month,

        TRY_TO_DECIMAL(weight_kg, 6, 2)             AS weight_kg_raw,
        COALESCE(TRY_TO_DECIMAL(weight_kg, 6, 2), 0.0) AS weight_kg,
        COALESCE(TRY_TO_NUMBER(reps), 0)            AS reps,
        TRY_TO_DECIMAL(rpe, 4, 1)                   AS rpe,
        COALESCE(TRY_TO_DECIMAL(weight_kg, 6, 2), 0.0)
            * COALESCE(TRY_TO_NUMBER(reps), 0)      AS tonnage,

        DATEDIFF('minute', start_ts, end_ts)        AS session_duration_min,

        (COALESCE(superset_id, '') != '')           AS is_superset,
        (LOWER(set_type) = 'failure')               AS is_failure_set,
        (LOWER(set_type) = 'dropset')               AS is_dropset,

        -- an explicit "gym: <name>" tag always wins and works for any gym,
        -- including ones this model has never seen
        NULLIF(
            REPLACE(
                TRIM(REGEXP_SUBSTR(tag_text, 'gym:\\s*([a-z0-9_ ]+)', 1, 1, 'e', 1)),
                ' ', '_'
            ), ''
        )                                           AS gym_tag_explicit,
        tag_text,
        description,
        exercise_notes,
        _source,
        _loaded_at
    FROM parsed
),

-- resolve gym from the tag text. gym and gym_detail are derived from the
-- SAME expression so they can never disagree (previously gym_detail read
-- description + notes while gym read only description, which produced
-- impossible pairs like chennai/cult_afterburn).
tagged AS (
    SELECT
        t.*,
        CASE
            WHEN tag_text LIKE '%thrive%'       THEN 'thrive'
            WHEN tag_text LIKE '%cult chennai%' THEN 'cult_chennai'
            WHEN tag_text LIKE '%afterburn%'    THEN 'cult_afterburn'
            WHEN tag_text LIKE '%vr fitness%'   THEN 'vr_fitness'
            WHEN tag_text LIKE '%nitw%'         THEN 'nitw_gym'
            WHEN tag_text LIKE '%golds%'        THEN 'golds'
            WHEN tag_text LIKE '%jashs%'        THEN 'jashs_gym'
            WHEN tag_text LIKE '%blr%'          THEN 'blr_other'
            WHEN tag_text LIKE '%pune%'         THEN 'pune_other'
            ELSE gym_tag_explicit
        END AS gym_detail_tagged
    FROM typed t
),

resolved AS (
    SELECT
        tg.*,
        CASE gym_detail_tagged
            WHEN 'thrive'         THEN 'chennai'
            WHEN 'cult_chennai'   THEN 'chennai'
            WHEN 'cult_afterburn' THEN 'hyd'
            WHEN 'vr_fitness'     THEN 'hyd'
            WHEN 'nitw_gym'       THEN 'hyd'
            WHEN 'blr_other'      THEN 'blr_other'
            WHEN 'pune_other'     THEN 'pune_other'
            WHEN NULL             THEN NULL
            ELSE CASE WHEN gym_detail_tagged IS NULL THEN NULL ELSE 'other' END
        END AS gym_tagged
    FROM tagged tg
),

-- fall back to the travel timeline only when nothing was tagged
joined AS (
    SELECT
        r.*,
        gl.gym        AS gl_gym,
        gl.gym_detail AS gl_gym_detail
    FROM resolved r
    LEFT JOIN {{ source('bronze', 'gym_locations') }} gl
        ON r.workout_date BETWEEN gl.start_date AND gl.end_date
),

with_exercise AS (
    SELECT
        j.*,
        COALESCE(et.primary_muscle_group, 'unknown') AS primary_muscle,
        COALESCE(et.equipment_category,   'unknown') AS equipment_category,
        et.secondary_muscle_groups,
        COALESCE(et.is_custom, FALSE)                AS is_custom_exercise,
        (et.title IS NULL)                           AS is_unmapped_exercise
    FROM joined j
    LEFT JOIN {{ source('bronze', 'dim_exercise_templates') }} et
        ON LOWER(j.exercise_title) = LOWER(et.title)
)

SELECT
    -- grain
    workout_id,
    exercise_title,
    set_index,
    set_occurrence,

    -- session
    workout_title,
    start_time,
    end_time,
    workout_date,
    week_start,
    year,
    month,
    session_duration_min,

    -- set
    set_type,
    weight_kg,
    weight_kg_raw,
    reps,
    rpe,
    tonnage,
    superset_id,
    is_superset,
    is_failure_set,
    is_dropset,
    -- a set with no logged load contributes 0 tonnage; flag it so volume
    -- models can fall back to counting sets instead of trusting tonnage
    (weight_kg_raw IS NULL OR weight_kg_raw = 0) AS is_unloaded_set,

    -- exercise dimension
    primary_muscle,
    equipment_category,
    secondary_muscle_groups,
    is_custom_exercise,
    is_unmapped_exercise,

    -- gym: tag wins, travel timeline is the fallback
    -- travel entries in gym_locations carry a gym but no gym_detail, so fall
    -- back to the gym name itself; otherwise the same trip splits across a
    -- NULL bucket and a tagged bucket
    TRIM(COALESCE(gym_detail_tagged, NULLIF(gl_gym_detail, ''), gl_gym)) AS gym_detail,
    TRIM(COALESCE(gym_tagged,        gl_gym))        AS gym,
    (gym_detail_tagged IS NOT NULL)                  AS is_gym_tagged,

    exercise_notes,
    _source,
    _loaded_at
FROM with_exercise
