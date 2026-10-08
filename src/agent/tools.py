# src/agent/tools.py
import re

from src.config import OPENAI_API_KEY, CHROMA_PATH, EMBEDDING_MODEL
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_chroma import Chroma
from src.db.snowflake_client import get_conn


# ── Schema context for LLM SQL generation ────────────────────────────────────

GOLD_SCHEMA_CONTEXT = """
Four gold tables, each with exactly ONE grain. Pick the table whose grain
matches the question; do not join them unless you truly need to.

1. HEVY.GOLD.FCT_TRAINING_DAY — one row per CALENDAR DAY ("should I rest?")
   - workout_date, is_training_day, gym, gym_detail, gyms_trained, sessions
   - daily_tonnage, daily_sets, daily_exercises, muscles_trained, failure_sets
   - session_duration_min, tonnage_per_min, sets_last_7d
   - days_since_last_session, days_of_history, bodyweight_kg
   - rolling_7d_avg_tonnage, rolling_28d_avg_tonnage
   - fatigue_index, training_status
     (optimal / overreaching / undertraining / insufficient_data)
   REST DAYS ARE ROWS: is_training_day = FALSE and daily_tonnage = 0. They are
   part of the ratio — never filter them out, and never filter on
   gym_detail IS NOT NULL (it is NULL on rest days).
   Load is systemic: tonnage sums across gyms, so gym_detail is context only.
   Use for: recovery, deload decisions, training load, rest patterns.

2. HEVY.GOLD.FCT_MUSCLE_WEEK — one row per (WEEK, MUSCLE) ("enough volume?")
   - week_start, year, muscle, movement_group (push/pull/legs/core/other)
   - direct_sets, indirect_sets, effective_sets (direct + 0.5 * indirect)
   - total_reps, total_tonnage, avg_weight_kg, max_weight_kg
   - unique_exercises, training_days, failure_sets, unloaded_sets
   - mev_sets, mav_sets, mrv_sets  (weekly volume landmarks)
   - volume_status
     (below_mev / optimal / above_mav / above_mrv / not_trained / not_tracked)
   - weeks_since_trained, sets_4wk_avg
   - prev_week_sets, prev_week_tonnage, tonnage_pct_change
   EVERY muscle appears in EVERY week, so a neglected muscle is a row with
   direct_sets = 0 and volume_status = 'not_trained'.
   NO GYM COLUMN — volume is systemic. Filter by muscle, not by gym.
   muscle values: chest, shoulders, triceps, lats, upper_back, biceps, traps,
   forearms, quadriceps, hamstrings, glutes, calves, adductors, abductors,
   abdominals, lower_back, neck, cardio, full_body, other, unknown
   Use for: per-muscle volume, MEV/MAV checks, neglected muscles, growth.

3. HEVY.GOLD.FCT_TRAINING_WEEK — one row per WEEK ("is my split balanced?")
   - week_start, year, training_days, total_sets, total_reps, total_tonnage
   - push_sets, pull_sets, leg_sets, core_sets, quad_sets, hamstring_sets
   - muscles_below_mev, muscles_not_trained, muscles_above_mrv
   - push_pull_ratio, push_pull_status
     (balanced / push_dominant / pull_dominant / no_pull_training)
   - quad_ham_ratio, quad_ham_status
     (balanced / quad_dominant_injury_risk / hamstring_dominant /
      no_hamstring_training)
   - prev_week_tonnage, tonnage_pct_change
   Use for: push/pull balance, weekly totals, split structure, injury risk.

4. HEVY.GOLD.FCT_EXERCISE_PROGRESSION
   one row per (EXERCISE, GYM_DETAIL, WORKOUT_DATE) ("am I getting stronger?")
   - workout_date, week_start, year, exercise_title, primary_muscle
   - equipment_category, movement_type, gym, gym_detail
   - sets_n, top_set_weight_kg, top_set_reps, exercise_tonnage
   - estimated_1rm, best_1rm_to_date, is_pr, days_since_pr, sessions_logged
   - e1rm_4wk_ago, e1rm_4wk_avg, e1rm_pct_change_4wk
   - bodyweight_kg, e1rm_per_kg_bodyweight
   - is_cross_gym_comparable, equipment_mismatch
   - progression_trend
     (improving / plateauing / declining / insufficient_data)
   GYM IS IN THE GRAIN here: a machine's load is not comparable between gyms,
   so compare PRs within one gym_detail, or filter
   is_cross_gym_comparable = TRUE to compare across gyms.
   Use for: PRs, e1RM trend, plateaus, relative strength.

Supporting silver tables:
- HEVY.SILVER.STG_SESSIONS  one row per workout_id: workout_date, gym_detail,
  total_sets, total_exercises, total_tonnage, session_duration_min,
  tonnage_per_min, muscles_trained. Use for session frequency/consistency.
- HEVY.SILVER.STG_SETS      one row per working set. Use only when the
  question needs individual sets (e.g. "what did I do last session?").

Rules:
- Use exact lowercase values for muscle, gym_detail, and all status columns.
- Exercise name matching: ILIKE '%exercise%'
- gym_detail values: thrive, vr_fitness, nitw_gym, cult_afterburn,
  cult_chennai, blr_other, pune_other, golds, jashs_gym
- fatigue_index > 1.2 = overreaching, < 0.8 = undertraining
- fatigue_index is a 7-CALENDAR-DAY vs 28-CALENDAR-DAY tonnage ratio
- training_status / progression_trend = 'insufficient_data' means unknown,
  not a real signal — do not interpret those rows.
- Always add a LIMIT.

IMPORTANT DEFAULTS — YOU MUST FOLLOW THESE, NO EXCEPTIONS:
1. FATIGUE/OVERTRAINING/DELOAD: WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
2. VOLUME / BALANCE:            WHERE week_start   >= DATEADD('week', -8, CURRENT_DATE)
3. PROGRESSION:                 WHERE workout_date >= DATEADD('day', -28, CURRENT_DATE)
4. PR questions: no date filter, show all time
BEFORE writing SQL, identify which category the question falls into and apply
the date filter. This is mandatory.
"""


# ── Query templates ───────────────────────────────────────────────────────────

QUERY_TEMPLATES = {
    "fatigue": """
        SELECT workout_date, is_training_day, gym_detail, daily_tonnage,
               daily_sets, days_since_last_session,
               rolling_7d_avg_tonnage, rolling_28d_avg_tonnage,
               fatigue_index, training_status
        FROM HEVY.GOLD.FCT_TRAINING_DAY
        WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
        ORDER BY workout_date DESC
        LIMIT 30
    """,
    "volume": """
        SELECT week_start, muscle, movement_group,
               direct_sets, indirect_sets, effective_sets,
               total_tonnage, mev_sets, mav_sets, volume_status,
               weeks_since_trained, tonnage_pct_change
        FROM HEVY.GOLD.FCT_MUSCLE_WEEK
        WHERE week_start >= DATEADD('week', -8, CURRENT_DATE)
        ORDER BY week_start DESC, direct_sets DESC
        LIMIT 200
    """,
    "balance": """
        SELECT week_start, training_days, total_sets,
               push_sets, pull_sets, leg_sets, core_sets,
               push_pull_ratio, push_pull_status,
               quad_ham_ratio, quad_ham_status,
               muscles_below_mev, muscles_not_trained
        FROM HEVY.GOLD.FCT_TRAINING_WEEK
        WHERE week_start >= DATEADD('week', -8, CURRENT_DATE)
        ORDER BY week_start DESC
        LIMIT 12
    """,
    "strength": """
        SELECT workout_date, exercise_title, gym_detail, primary_muscle,
               top_set_weight_kg, top_set_reps, estimated_1rm,
               best_1rm_to_date, is_pr, days_since_pr,
               e1rm_pct_change_4wk, progression_trend,
               e1rm_per_kg_bodyweight, is_cross_gym_comparable
        FROM HEVY.GOLD.FCT_EXERCISE_PROGRESSION
        WHERE workout_date >= DATEADD('day', -28, CURRENT_DATE)
        ORDER BY workout_date DESC, estimated_1rm DESC
        LIMIT 100
    """,
    "pr": """
        SELECT exercise_title, gym_detail, primary_muscle, equipment_category,
               MAX(best_1rm_to_date)                       AS best_e1rm,
               MAX(top_set_weight_kg)                      AS heaviest_weight_kg,
               MAX(CASE WHEN is_pr THEN workout_date END)  AS last_pr_date,
               ANY_VALUE(is_cross_gym_comparable)          AS is_cross_gym_comparable,
               COUNT(*)                                    AS sessions_logged
        FROM HEVY.GOLD.FCT_EXERCISE_PROGRESSION
        GROUP BY exercise_title, gym_detail, primary_muscle, equipment_category
        ORDER BY best_e1rm DESC
        LIMIT 100
    """,
    "sessions": """
        SELECT workout_date, workout_title, gym_detail,
               total_sets, total_exercises, muscles_trained,
               total_tonnage, session_duration_min, tonnage_per_min
        FROM HEVY.SILVER.STG_SESSIONS
        WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
        ORDER BY workout_date DESC
        LIMIT 30
    """,
    "raw_sets": """
        SELECT workout_date, workout_title, gym_detail,
               exercise_title, primary_muscle, set_index, set_type,
               weight_kg, reps, tonnage
        FROM HEVY.SILVER.STG_SETS
        WHERE workout_date >= DATEADD('day', -7, CURRENT_DATE)
        ORDER BY workout_date DESC, exercise_title, set_index
        LIMIT 200
    """,
}


def classify_question(question: str) -> str:
    """
    Route a question to the template whose grain matches it.

    Order matters: the most specific intent is checked first. "What did I do
    last session?" must reach raw_sets before the word "session" sends it to
    the session-frequency template.

    Two matchers, deliberately:
      has()  exact word match, for short or ambiguous tokens. A substring
             test for "pr" also fires on "progress" and "press".
      stem() prefix match, so "progress" also catches "progressed" and
             "progressing". Only safe for longer, unambiguous stems.
    """
    q = question.lower()

    def has(*words: str) -> bool:
        return any(re.search(r"\b" + re.escape(w) + r"\b", q) for w in words)

    def stem(*stems: str) -> bool:
        return any(re.search(r"\b" + re.escape(st) + r"\w*", q) for st in stems)

    # individual sets from a specific recent session
    if has("last workout", "last session", "yesterday", "what did i do",
           "sets i did", "today's workout"):
        return "raw_sets"

    # all-time bests — exact match only, "pr" is a substring of many words
    if has("pr", "prs", "1rm", "personal record", "personal best",
           "heaviest", "max weight", "best lift", "one rep max"):
        return "pr"

    # recovery and training load
    if stem("overtrain", "deload", "fatigue", "recover", "burnout") \
       or has("tired", "rest day", "should i rest", "burnt out", "burned out"):
        return "fatigue"

    # strength trend over time
    if stem("stronger", "progress", "plateau", "improv", "gain", "e1rm") \
       or has("getting stronger"):
        return "strength"

    # naming specific muscles needs the per-muscle grain, not the weekly
    # rollup: fct_training_week only counts how many are below MEV,
    # fct_muscle_week says which ones
    if stem("neglect") or has("which muscle", "which muscles",
                              "what muscle", "what muscles"):
        return "volume"

    # split structure and push/pull symmetry
    if stem("balance", "imbalance", "symmetr") \
       or has("push", "pull", "ratio", "split"):
        return "balance"

    # per-muscle weekly volume
    if stem("volume", "hypertroph", "grow") \
       or has("sets", "enough", "mev", "mav"):
        return "volume"

    # training frequency and consistency
    if stem("session", "frequen", "consisten") \
       or has("how often", "last week", "recent", "this week"):
        return "sessions"

    return "llm"


# ── Snowflake tool ────────────────────────────────────────────────────────────

def snowflake_tool(question: str) -> dict:
    """
    Routes to a hardcoded template for common questions,
    falls back to LLM-generated SQL for open-ended ones.
    """
    template_key = classify_question(question)

    if template_key != "llm":
        sql = QUERY_TEMPLATES[template_key]
        print(f"Using template: {template_key}")
    else:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=OPENAI_API_KEY,
            temperature=0,
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""
You are a Snowflake SQL expert. Generate a single SQL query to answer the user's question.
Use only the tables described below. Return ONLY the SQL query, nothing else.
No markdown, no explanation, no backticks.

{GOLD_SCHEMA_CONTEXT}
            """),
            ("human", "{question}"),
        ])
        chain = prompt | llm | StrOutputParser()
        sql   = chain.invoke({"question": question}).strip()
        print(f"LLM-generated SQL:\n{sql}\n")

    try:
        conn   = get_conn()
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows    = cursor.fetchall()
        cursor.close()
        conn.close()
        return {
            "sql":     sql,
            "results": [dict(zip(columns, row)) for row in rows],
            "error":   None,
        }

    except Exception as e:
        return {
            "sql":     sql,
            "results": [],
            "error":   str(e),
        }


# ── ChromaDB tool ─────────────────────────────────────────────────────────────

def chromadb_tool(question: str, k: int = 4) -> list[dict]:
    """
    Searches PubMed paper embeddings for content
    relevant to the question. Returns top k chunks.
    """
    embeddings   = OpenAIEmbeddings(
        model   = EMBEDDING_MODEL,
        api_key = OPENAI_API_KEY,
    )
    vector_store = Chroma(
        collection_name    = "fitness_papers",
        embedding_function = embeddings,
        persist_directory  = CHROMA_PATH,
    )

    docs = vector_store.similarity_search(question, k=k)

    return [
        {
            "title":   doc.metadata.get("title", ""),
            "content": doc.page_content,
            "source":  doc.metadata.get("source", ""),
            "pmid":    doc.metadata.get("pmid", ""),
        }
        for doc in docs
    ]