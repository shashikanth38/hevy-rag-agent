# hevy-rag-agent — Project Documentation

## What this project is

A fitness intelligence platform combining a full data engineering pipeline with an agentic RAG system. Ingests workout data from Hevy, stores in Snowflake medallion architecture, transforms with dbt, and powers a LangGraph agent that retrieves PubMed sports science papers for personalised training recommendations.

## GitHub
https://github.com/shashikanth38/hevy-rag-agent

## Project root
/Users/shashikanthg/Documents/projects/rag_hevy/hevy-rag-agent

## Always run Python from project root
```bash
cd /Users/shashikanthg/Documents/projects/rag_hevy/hevy-rag-agent
source .venv/bin/activate
```

## Always run dbt from
```bash
cd /Users/shashikanthg/Documents/projects/rag_hevy/hevy-rag-agent/dbt/hevy_dbt
```

## Weekly pipeline
```bash
python -m src.ingestion.hevy_sync
python -m src.ingestion.measurements_loader
cd dbt/hevy_dbt && uv run dbt build   # run + test in dependency order
```

---

## Snowflake

- Account: PETQFKW-AI43376 (AWS ap-southeast-1, Singapore)
- Database: HEVY, Role: HEVY_ROLE, Warehouse: HEVY_WH (XSmall)
- Auth: RSA key-pair (rsa_key.p8, no passphrase)
- Connection helper: src/db/snowflake_client.py → get_conn()

### Schema structure
```
HEVY
├── BRONZE    raw data as-is
├── SILVER    cleaned and typed (dbt incremental)
├── GOLD      agent-ready features (dbt table)
└── STAGING   dbt ephemeral
```

---

## AWS / S3

- Bucket: hevy-data-agentproj (ap-southeast-1)
- IAM role: hevy-snowflake-role (arn:aws:iam::622247620310:role/hevy-snowflake-role)
- Stage: HEVY.BRONZE.HEVY_STAGE
- S3 paths:
  - workouts/historical/ — one-time CSV load
  - workouts/incremental/<timestamp>/ — weekly API sync

---

## Bronze tables

| Table | Rows | Source |
|---|---|---|
| RAW_WORKOUTS | 19,535 | CSV + API |
| DIM_EXERCISE_TEMPLATES | 438 | Hevy API |
| GYM_LOCATIONS | 64 | Manual from travel history |
| RAW_MEASUREMENTS | 39 | Hevy API |
| SYNC_META | 1 | Pipeline watermark |
| RAW_PAPERS | 0 | Unused — papers live in ChromaDB |

### What bronze actually supports (profiled)
- 164 distinct exercises; 99.9% join to DIM_EXERCISE_TEMPLATES on LOWER(title).
  Only 14 sets across 4 exercises are unmapped.
- Equipment mix: machine 67%, dumbbell 22%, barbell 7.5%, bodyweight 2.2%.
  Because machines dominate and machine load is not comparable between gyms,
  cross-gym PR comparison only applies to ~30% of sets.
- set_type: normal 89.3%, warmup 7.7%, dropset 2.8%, failure 0.2%.
  Warmups are excluded from silver; dropsets are NOT (they are real volume).
- DIM_EXERCISE_TEMPLATES.secondary_muscle_groups is a JSON array and is used
  for indirect volume credit in fct_muscle_week.
- RPE is present on 20 of 17,952 sets (0.1%) — unusable until logged
  consistently. Supersets: 57 sets. body_fat_pct: empty in all rows.

### GYM_LOCATIONS
Derived from Redbus bus booking emails + IRCTC train tickets in Gmail.
Maps date ranges to gym locations.

| gym | gym_detail | Period |
|---|---|---|
| hyd | nitw_gym | until May 20, 2023 |
| hyd | vr_fitness | May 21, 2023 – Nov 16, 2025 |
| hyd | cult_afterburn | Nov 17, 2025 – present |
| chennai | thrive | main Chennai gym |
| chennai | cult_chennai | secondary |
| blr_other | NULL | random Bangalore |
| pune_other | NULL | one-off Pune |

### Going forward — gym tagging
Write the gym in the Hevy workout description (or exercise notes — stg_sets
scans both). Recognised aliases:
- thrive, cult chennai, afterburn, vr fitness, nitw, golds, jashs, blr, pune

For any gym with no alias, use an explicit prefix: `gym: <name>`.
stg_sets extracts it with a regex, so a new gym needs no code change.
Writing nothing means the workout inherits whatever gym the GYM_LOCATIONS
date range says — currently hyd/cult_afterburn for anything after
Jun 30 2026 — so an untagged away-gym session is silently mislabelled.

### SYNC_META watermark
```sql
UPDATE HEVY.BRONZE.SYNC_META
SET value = '<ISO timestamp>'
WHERE key = 'last_workout_sync';
```

---

## Silver models

Unique key: **[workout_id, exercise_title, set_index, set_occurrence]**.
Incremental filter on `_loaded_at` (not workout_date) so edits to older
workouts are picked up.

| Model | Rows | Purpose |
|---|---|---|
| stg_sets | 17,952 | One row per working set. The single source of truth. |
| stg_sessions | 1,038 | One row per workout_id |
| stg_bodyweight_daily | 1,280 | Daily bodyweight, linearly interpolated |
| stg_measurements | 39 | Cast types |

### Why set_occurrence exists
The natural key collides. Doing the same exercise twice in one session gives
both blocks set_index 0,1,2..., and the CSV surrogate workout_id
(MD5 of title + start_time) cannot separate them. stg_sets therefore:
1. collapses rows that are byte-identical on (key + set_type + weight + reps)
   — a re-sync overlap produced 76 such copies; then
2. numbers whatever still shares a key as set_occurrence 1, 2, ...

Before this, silver held 95 duplicate-key rows counted twice in every
tonnage, volume and fatigue number. dbt's incremental merge cannot dedupe
*within* one incoming batch, so they passed silently. The singular test
`assert_stg_sets_grain_is_unique` now guards it.

### Working sets include dropsets
`set_type IN ('normal','failure','dropset')`. Excluding dropsets discarded
541 real working sets. Warmups stay excluded.

### Gym resolution
Priority: explicit `gym: <name>` tag → known alias → GYM_LOCATIONS date
range → NULL. `gym` and `gym_detail` derive from the SAME expression, so they
cannot disagree. Previously gym_detail read description + notes while gym
read only description, producing impossible pairs (chennai/cult_afterburn,
hyd/thrive) and a `'hyd   '` value with trailing spaces that no `= 'hyd'`
filter matched.

### Datetime handling
Parsed once in a CTE, not re-parsed per column:
```sql
COALESCE(
    TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),  -- CSV
    TRY_TO_TIMESTAMP_NTZ(start_time)                            -- API ISO 8601
)
```

---

## Gold models

All full-refresh (window functions need full history).
**One grain per model.** Pick the table whose grain matches the question.

| Model | Grain | Rows | Answers |
|---|---|---|---|
| fct_training_day | calendar day | 1,280 | should I rest? |
| fct_training_week | week | 184 | is my split balanced? |
| fct_muscle_week | week × muscle | 3,864 | enough volume for X? |
| fct_exercise_progression | exercise × gym × date | 5,814 | am I getting stronger? |

Replaced `weekly_volume`, `muscle_balance`, `fatigue_index` and
`strength_progression`.

### Seed: muscle_landmarks
`seeds/muscle_landmarks.csv` holds MEV/MAV/MRV weekly set targets and the
push/pull/legs/core classification for all 21 muscle values. **Retuning a
threshold or adding a muscle is a CSV edit, not a SQL change.**

This replaced 24 hardcoded boolean columns in muscle_balance
(chest_below_mev, chest_neglected, ...) which covered only 8 of 20 trained
muscles — calves, abdominals, traps, forearms and glutes were invisible.

### fct_muscle_week
- Muscles are ROWS, not columns. Every (week, muscle) pair exists, so a
  neglected muscle is a row with `direct_sets = 0` and
  `volume_status = 'not_trained'` rather than an absent row.
- `effective_sets = direct_sets + 0.5 * indirect_sets`, where indirect comes
  from the template's `secondary_muscle_groups` JSON array. This data was
  previously unused, and it matters: glutes have 111 direct sets but 1,286
  indirect, forearms 286 vs 2,727.
- **No gym column.** Volume is systemic. Splitting by gym fragmented the data
  and broke week-over-week comparison.
- `tonnage_pct_change` joins to `week_start - 7 days` at the full grain.

### fct_training_day — read before changing
- Grain is a CALENDAR DATE SPINE: one row per day from first to last training
  day, rest days included as `daily_tonnage = 0`, `is_training_day = FALSE`.
- Windows use `RANGE BETWEEN INTERVAL`, never `ROWS`. A ROWS frame counts
  sessions instead of days, so "7d" silently meant ~2.3 weeks at 3
  sessions/week and time off never lowered the acute average. `AVG` over a
  RANGE frame also divides by rows present, so the zero-fill is needed for
  the denominator, not just the span.
- Load is SYSTEMIC: tonnage sums across gyms. Fatigue belongs to the body,
  not the venue, so there is no PARTITION BY gym. `gym_detail` is the
  dominant gym of that day — context only, never a grouping key.
- The spine ends at the last training day, not CURRENT_DATE, so a stale
  pipeline is not misreported as months of deliberate rest.
- **Never filter `gym_detail IS NOT NULL`** on this table: it is NULL on rest
  days, and that filter removes exactly the signal the model adds.

### fatigue_index thresholds (fct_training_day)
- `> 1.2` overreaching, `< 0.8` undertraining, else optimal
- `'insufficient_data'` for the first 27 days, where the 28-day window is not
  yet full and the ratio is biased toward 1.0
- Current distribution: optimal 71%, overreaching 13.7%, undertraining 13.2%

### fct_exercise_progression
- Gym IS in the grain: machine load is not comparable between venues.
- `is_cross_gym_comparable = TRUE` for barbell, dumbbell, kettlebell only.
- Trend windows use RANGE on workout_date. The previous model used
  `ROWS BETWEEN 28 PRECEDING AND 22 PRECEDING` to mean "4 weeks ago", which
  is 28 *sessions* — roughly 7 months for a once-weekly exercise.
- `e1rm_per_kg_bodyweight` uses stg_bodyweight_daily.

---

## dbt commands

```bash
uv run dbt run                    # incremental (weekly)
uv run dbt run --full-refresh     # full rebuild
uv run dbt run --select stg_sets  # single model
uv run dbt test                   # data quality tests
uv run dbt debug                  # test connection
```

### dbt test results
`uv run dbt build` → 69 PASS, 0 WARN, 0 ERROR
(1 seed + 3 silver + 5 tables + 60 data tests) in ~9s

Singular tests in `tests/` guard the grain bugs that previously went
unnoticed: grain uniqueness on stg_sets / fct_muscle_week /
fct_exercise_progression, fatigue_index plausibility range, no gaps in the
fct_training_day date spine, and no warmups leaking into silver.

---

## LangGraph agent

### State
```python
class AgentState(TypedDict):
    question:     str
    sql_results:  dict
    paper_chunks: list[dict]
    answer:       str
    citations:    list[dict]
    confidence:   str
    retry_count:  int
    tools_to_use: list[str]
    response:     Any  # AgentResponse Pydantic
```

### Graph flow
```
START → supervisor → snowflake (if needed)
                   → chromadb  (if needed)
                   → synthesis → END (or retry)
```

### Pydantic output
```python
class AgentResponse(BaseModel):
    recommendation: str
    your_numbers:   str
    evidence:       str
    action:         str
    confidence:     str  # low/medium/high
```

### SQL strategy
- 7 templates with enforced date filters: fatigue, volume, balance, strength,
  pr, sessions, raw_sets — one per gold grain
- Open-ended questions fall through to LLM-generated SQL
- `classify_question()` in tools.py routes between the two. It checks the most
  specific intent first ("last session" must reach raw_sets before the word
  "session" sends it to the frequency template) and uses two matchers:
  exact word match for short/ambiguous tokens ("pr" is a substring of
  "progress" and "press") and prefix match for longer stems so "progress"
  also catches "progressed".

### Date defaults
- fatigue/overtraining: last 30 days
- volume: last 8 weeks
- progression: last 28 days
- PR: all time

---

## Hevy API constraints

- pageSize max = 10 (not 100 as documented)
- Events endpoint needs since param, URL-encoded colons cause 400 — URL built manually
- workout_id is a real UUID in API responses
- CSV rows use MD5(title || '|' || start_time) as surrogate workout_id

---

## Streamlit UI

```bash
streamlit run app/streamlit_app.py
```

Dark theme chat interface. Sidebar has suggested questions. Renders structured AgentResponse with recommendation, numbers, evidence, action, confidence badge, clickable citations.

---

## Phase status

| Phase | Status |
|---|---|
| 1A: Snowflake + S3 + CSV load | Complete |
| 1B: dbt silver + gold + tests | Complete |
| 1C: Airflow Docker Compose DAGs | Pending |
| 2: LangGraph agent | Complete |
| 3: Streamlit UI | Complete |

---

## Data as of Oct 3, 2026

Training history: Feb 10, 2023 → Oct 3, 2026
(1,332 calendar days, 1,073 training days, 259 rest days, 1,074 sessions)

| Layer | Table | Rows |
|---|---|---|
| bronze | RAW_WORKOUTS | 20,165 |
| silver | stg_sets | 18,491 |
| silver | stg_sessions | 1,074 |
| silver | stg_measurements | 43 |
| gold | fct_training_day | 1,332 |
| gold | fct_training_week | 191 |
| gold | fct_muscle_week | 4,011 |
| gold | fct_exercise_progression | 6,201 |

training_status: optimal 71.3% · overreaching 13.5% · undertraining 13.1% ·
insufficient_data 2.0%

Bodyweight: 77.0 kg (2025) → 72.8 kg (Sep 28, 2026)

Chronically under-volumed muscles (below MEV in the latest week):
calves, lats, upper_back, traps. forearms last trained directly 19 weeks ago;
glutes 4 weeks ago (both get substantial indirect work).

### Gotcha: the COPY result column order
`COPY INTO` returns status, rows_parsed, rows_loaded, **error_limit**,
**errors_seen** — error_limit is index 4 and errors_seen is index 5. With
`ON_ERROR = 'CONTINUE'` error_limit equals the row count, so reading index 4
makes a perfectly clean load look like a total failure. `copy_into_bronze`
reads these by NAME from `cursor.description`; never go back to positions.

Confirm a load independently with:
```sql
SELECT file_name, status, row_count, row_parsed, error_count, first_error_message
FROM TABLE(INFORMATION_SCHEMA.COPY_HISTORY(
    TABLE_NAME => 'HEVY.BRONZE.RAW_WORKOUTS',
    START_TIME => DATEADD('hour', -3, CURRENT_TIMESTAMP())))
ORDER BY last_load_time DESC;
```
`VALIDATE(..., JOB_ID => '_last')` only works inside the session that ran the
COPY, so it is useless from a fresh connection.

### Gotcha: the connector autocommits
`COPY INTO` is committed the moment it runs, so `conn.rollback()` cannot undo
a partial load. The real protection is that `hevy_sync` does not advance the
watermark unless the COPY reports status='Loaded' with 0 errors — the window
stays repeatable, and silver collapses exact duplicate rows.
