# Hevy Fitness Intelligence Platform

An end-to-end data engineering + AI project that ingests 3.5 years of personal workout data from the Hevy API, transforms it through a Snowflake medallion architecture using dbt, and powers a LangGraph agentic RAG system that answers training questions grounded in both personal data and published sports science research.

---

## What it does

Ask natural language questions about your training:

> *"Am I doing enough volume for leg growth?"*
> → Queries your Snowflake gold tables for the last 8 weeks of quadriceps/hamstrings volume, retrieves relevant PubMed research on hypertrophy set thresholds, and synthesises a personalised recommendation with citations.

> *"Should I deload this week?"*
> → Computes your 7-day vs 28-day rolling tonnage ratio (fatigue index), cross-references deloading research, and recommends action.

> *"How has my squat progressed over the last month?"*
> → Queries PR tracking per gym, surfaces week-by-week weight progression with trend analysis.

---

## Architecture

```
Hevy API (weekly incremental)          PubMed API (weekly)
        ↓                                      ↓
   hevy_sync.py                      pubmed_fetcher.py
   (S3 → COPY INTO)                   (embed → ChromaDB)
        ↓
┌─────────────────────────────────────┐
│         Snowflake (HEVY DB)         │
│  Bronze → Silver → Gold             │
│  raw_workouts  stg_sets  weekly_volume        │
│  dim_exercise_templates  pr_tracking          │
│  gym_locations  fatigue_index                 │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│         LangGraph Agent             │
│  supervisor → snowflake_tool        │
│             → chromadb_tool         │
│             → synthesis             │
│  Pydantic structured output         │
└─────────────────────────────────────┘
        ↓
   Streamlit chat UI (dark theme)
```

---

## Tech stack

| Layer | Tool |
|---|---|
| Data warehouse | Snowflake (AWS ap-southeast-1) |
| Staging | AWS S3 |
| Transformation | dbt-snowflake |
| Orchestration | Airflow via Docker Compose (WIP) |
| Vector store | ChromaDB (local) |
| Embeddings | OpenAI text-embedding-3-small |
| LLM | GPT-4o-mini |
| Agent framework | LangGraph |
| UI | Streamlit |
| Language | Python 3.11, uv |

---

## Data pipeline

### Bronze — raw ingestion
- `raw_workouts` — one row per set, loaded from CSV (historical) and Hevy API (weekly incremental)
- `raw_measurements` — body weight from Hevy API
- `dim_exercise_templates` — 438 exercise templates with muscle groups and equipment categories
- `gym_locations` — 63-row lookup table mapping date ranges to gym locations, derived from Redbus and IRCTC travel history
- `sync_meta` — watermark table tracking last successful API sync timestamp

### Silver — cleaned and typed
- `stg_sets` — normal + failure sets, warmups excluded, dual datetime format parsing, gym detection with date-join fallback, tonnage computed
- `stg_sessions` — one row per workout session, deduplicated
- `stg_measurements` — cleaned body measurements

### Gold — agent-ready features
- `weekly_volume` — weekly sets/tonnage per muscle group per gym with week-over-week % change
- `pr_tracking` — personal records per exercise per gym with cross-gym comparability flag
- `fatigue_index` — 7-day vs 28-day rolling tonnage ratio with training status classification

---

## Key design decisions

**ELT not ETL** — raw data lands in Snowflake exactly as received. All transformation happens inside Snowflake via dbt.

**Gym-aware PR tracking** — PRs partitioned by `gym_detail` because machine weights are not comparable across gyms. Only barbell, dumbbell, smith machine, and weighted bodyweight have `is_cross_gym_comparable = TRUE`.

**Gym location from travel history** — Hevy has no location field. Gym tags reconstructed from 3 years of Redbus bus booking emails and IRCTC train tickets, stored as a date-range lookup table joined in silver.

**Incremental silver, full-refresh gold** — Silver uses dbt incremental materialisation (merge on `workout_id + exercise_title + set_index`). Gold uses full refresh because window functions require full history.

**Query templates over LLM SQL for time-sensitive metrics** — Fatigue, volume, and session queries use hardcoded SQL templates with enforced date windows. LLM SQL only for open-ended exploratory questions.

**Agentic RAG over vanilla RAG** — Supervisor node dynamically decides which tools to call. Personal data questions go to Snowflake. Science questions go to ChromaDB. Most questions use both.

---

## Project structure

```
hevy-rag-agent/
├── src/
│   ├── ingestion/
│   │   ├── csv_loader.py
│   │   ├── hevy_sync.py
│   │   ├── measurements_loader.py
│   │   ├── exercise_template_loader.py
│   │   └── pubmed_fetcher.py
│   ├── agent/
│   │   ├── state.py
│   │   ├── tools.py
│   │   ├── nodes.py
│   │   └── graph.py
│   ├── db/
│   │   └── snowflake_client.py
│   └── config.py
├── dbt/hevy_dbt/models/
│   ├── bronze/sources.yml
│   ├── silver/
│   └── gold/
├── app/streamlit_app.py
├── CLAUDE.md
└── pyproject.toml
```

---

## Running locally

```bash
git clone https://github.com/shashikanth38/hevy-rag-agent.git
cd hevy-rag-agent
uv venv && source .venv/bin/activate
uv sync
```

Create `.env` with your credentials, then:

```bash
# one-time setup
python -m src.ingestion.csv_loader
python -m src.ingestion.exercise_template_loader
python -m src.ingestion.pubmed_fetcher
cd dbt/hevy_dbt && uv run dbt run --full-refresh

# weekly sync
python -m src.ingestion.hevy_sync
python -m src.ingestion.measurements_loader
cd dbt/hevy_dbt && uv run dbt run

# launch UI
streamlit run app/streamlit_app.py
```

---

## Data as of Jun 2026

| Table | Rows |
|---|---|
| raw_workouts | 18,841 sets |
| stg_sets | 16,903 sets |
| stg_sessions | 1,001 sessions |
| weekly_volume | 3,107 rows |
| pr_tracking | 5,932 rows |
| fatigue_index | 1,013 rows |

Training history: Feb 2023 – Jun 2026 (3.5 years, 1,001 sessions)