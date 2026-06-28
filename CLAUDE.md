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
cd dbt/hevy_dbt && uv run dbt run
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
| RAW_WORKOUTS | 18,841 | CSV + API |
| RAW_MEASUREMENTS | 39 | Hevy API |
| DIM_EXERCISE_TEMPLATES | 438 | Hevy API |
| GYM_LOCATIONS | 63 | Manual from travel history |
| SYNC_META | 1 | Pipeline watermark |

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
Write gym name in Hevy workout description field:
- thrive, cult chennai, afterburn, vr fitness, blr, pune

### SYNC_META watermark
```sql
UPDATE HEVY.BRONZE.SYNC_META
SET value = '<ISO timestamp>'
WHERE key = 'last_workout_sync';
```

---

## Silver models

All incremental. Unique key: [workout_id, exercise_title, set_index].
Incremental filter on _loaded_at (not start_time) to catch updated workouts.

| Model | Rows | Key transformations |
|---|---|---|
| stg_sets | 16,903 | Parse both datetime formats, gym join, tonnage, filter warmups |
| stg_sessions | 1,001 | One row per session, dedup with QUALIFY |
| stg_measurements | 39 | Cast types |

### Datetime handling in stg_sets
```sql
COALESCE(
    TRY_TO_TIMESTAMP(start_time, 'MON DD, YYYY, HH12:MI PM'),  -- CSV
    TRY_TO_TIMESTAMP_NTZ(start_time)                            -- API ISO 8601
)
```

---

## Gold models

All full-refresh (window functions need full history).

| Model | Rows | Used for |
|---|---|---|
| weekly_volume | 3,107 | Volume by muscle by week |
| pr_tracking | 5,932 | PRs per exercise per gym |
| fatigue_index | 1,013 | 7d vs 28d tonnage ratio |

### fatigue_index thresholds
- > 1.2 = overreaching
- < 0.8 = undertraining
- else = optimal

### pr_tracking
- is_cross_gym_comparable = TRUE only for barbell, dumbbell, smith_machine, weighted_bodyweight
- Machines not comparable across gyms

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
17 PASS, 1 WARN (not_null on pr_tracking.gym_detail — expected for historical data), 0 ERROR

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
- Common questions (fatigue, volume, sessions, PR) use hardcoded templates with enforced date filters
- Open-ended questions use LLM-generated SQL
- classify_question() in tools.py routes between the two

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

## Data as of Jun 24, 2026

Training history: Feb 10, 2023 → Jun 24, 2026 (3.5 years, 1,001 sessions)
Top muscles: shoulders (372 weekly rows), triceps (358), chest (350), biceps (338)