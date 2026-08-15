# src/agent/tools.py
from src.config import OPENAI_API_KEY, CHROMA_PATH, EMBEDDING_MODEL
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_chroma import Chroma
from src.db.snowflake_client import get_conn


# ── Schema context for LLM SQL generation ────────────────────────────────────

GOLD_SCHEMA_CONTEXT = """
You have access to these Snowflake gold tables:

1. HEVY.GOLD.WEEKLY_VOLUME
   - week_start, gym, gym_detail, primary_muscle, equipment_category
   - total_sets, total_reps, total_tonnage, avg_weight_kg, max_weight_kg
   - tonnage_pct_change (week over week %)
   Use for: volume trends, muscle group analysis, progressive overload signals
   primary_muscle values: 'full_body','traps','glutes','biceps','forearms',
   'hamstrings','chest','upper_back','shoulders','quadriceps'

2. HEVY.GOLD.FATIGUE_INDEX
   - workout_date, gym, gym_detail
   - daily_tonnage, rolling_7d_avg_tonnage, rolling_28d_avg_tonnage
   - fatigue_index, training_status (optimal/overreaching/undertraining)
   Use for: recovery status, deload decisions, training load management

3. HEVY.SILVER.STG_SESSIONS
   - workout_title, workout_date, gym, gym_detail
   - session_duration_min, total_sets, total_exercises, total_tonnage
   Use for: session frequency, training consistency

4. HEVY.GOLD.STRENGTH_PROGRESSION
   - exercise_title, gym_detail, workout_date, week_start
   - max_weight_kg, reps_at_max_weight, estimated_1rm
   - cumulative_pr_e1rm, is_pr, weeks_since_pr
   - e1rm_4wk_ago, e1rm_pct_change_4wk, e1rm_rolling_4wk_avg
   - progression_trend (improving/plateauing/declining/insufficient_data/deload)
   - est_weeks_to_next_pr, equipment_mismatch, movement_type
   Use for: strength trends, e1RM progression, plateau detection, PR prediction

5. HEVY.GOLD.MUSCLE_BALANCE
   - week_start, gym_detail
   - chest_sets, shoulder_sets, tricep_sets, bicep_sets
   - lat_sets, upper_back_sets, quad_sets, hamstring_sets
   - push_sets, pull_sets, leg_sets
   - push_pull_ratio, quad_ham_ratio, bicep_tricep_ratio
   - push_pull_status (balanced/push_dominant/pull_dominant)
   - quad_ham_status (balanced/quad_dominant — injury risk/hamstring_dominant)
   - chest_below_mev, shoulders_below_mev, quads_below_mev (TRUE/FALSE)
   - chest_neglected, biceps_neglected etc (TRUE/FALSE — no sets that week)
   Use for: muscle balance, injury risk, volume landmark analysis

Rules:
- Always filter by gym_detail when comparing PRs (use exact lowercase gym names)
- For exercise name matching use ILIKE '%exercise%'
- fatigue_index > 1.2 = overreaching, < 0.8 = undertraining
- Return only columns relevant to the question
- gym_detail values: 'thrive','cult_chennai','cult_afterburn','vr_fitness','nitw_gym'

IMPORTANT DEFAULTS — YOU MUST FOLLOW THESE, NO EXCEPTIONS:
1. FATIGUE/OVERTRAINING/DELOAD: WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
2. VOLUME: WHERE week_start >= DATEADD('week', -8, CURRENT_DATE)
3. PROGRESSION: WHERE workout_date >= DATEADD('day', -28, CURRENT_DATE)
4. PR questions: no date filter, show all time
BEFORE writing SQL, identify which category the question falls into and apply the date filter. This is mandatory.
"""


# ── Query templates ───────────────────────────────────────────────────────────

QUERY_TEMPLATES = {
    "fatigue": """
        SELECT workout_date, gym_detail, fatigue_index, training_status,
               rolling_7d_avg_tonnage, rolling_28d_avg_tonnage
        FROM HEVY.GOLD.FATIGUE_INDEX
        WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
        AND gym_detail IS NOT NULL
        ORDER BY workout_date DESC
        LIMIT 14
    """,
    "volume": """
        SELECT week_start, gym_detail, primary_muscle,
               total_sets, total_tonnage, tonnage_pct_change
        FROM HEVY.GOLD.WEEKLY_VOLUME
        WHERE week_start >= DATEADD('week', -8, CURRENT_DATE)
        AND gym_detail IS NOT NULL
        ORDER BY week_start DESC, total_tonnage DESC
    """,
    "sessions": """
        SELECT workout_date, workout_title, gym_detail,
               total_sets, total_tonnage, session_duration_min
        FROM HEVY.SILVER.STG_SESSIONS
        WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
        AND gym_detail IS NOT NULL
        ORDER BY workout_date DESC
    """,
    "strength": """
        SELECT exercise_title, gym_detail, workout_date,
               max_weight_kg, reps_at_max_weight, estimated_1rm,
               cumulative_pr_e1rm, e1rm_pct_change_4wk,
               progression_trend, est_weeks_to_next_pr
        FROM HEVY.GOLD.STRENGTH_PROGRESSION
        WHERE workout_date >= DATEADD('day', -28, CURRENT_DATE)
        AND gym_detail IS NOT NULL
        ORDER BY workout_date DESC
    """,
    "balance": """
        SELECT week_start, gym_detail,
               chest_sets, shoulder_sets, tricep_sets, bicep_sets,
               lat_sets, upper_back_sets, quad_sets, hamstring_sets,
               push_pull_ratio, quad_ham_ratio, bicep_tricep_ratio,
               push_pull_status, quad_ham_status,
               chest_below_mev, shoulders_below_mev, quads_below_mev,
               hamstrings_below_mev, biceps_below_mev, lats_below_mev,
               chest_neglected, shoulders_neglected, quads_neglected,
               hamstrings_neglected, biceps_neglected, lats_neglected
        FROM HEVY.GOLD.MUSCLE_BALANCE
        WHERE week_start >= DATEADD('week', -6, CURRENT_DATE)
        AND gym_detail IS NOT NULL
        ORDER BY week_start DESC
    """,
    "raw_sets": """
    SELECT workout_date, workout_title, gym_detail,
           exercise_title, set_index, set_type,
           weight_kg, reps, tonnage
    FROM HEVY.SILVER.STG_SETS
    WHERE workout_date >= DATEADD('day', -7, CURRENT_DATE)
    AND gym_detail IS NOT NULL
    ORDER BY workout_date DESC, exercise_title, set_index
"""
}


def classify_question(question: str) -> str:
    """Route question to the right template."""
    q = question.lower()
    if any(w in q for w in ["overtrain", "deload", "fatigue", "recovery", "tired"]):
        return "fatigue"
    if any(w in q for w in ["balance", "push", "pull", "ratio", "neglect", "muscle group"]):
        return "balance"
    if any(w in q for w in ["stronger", "progress", "e1rm", "1rm", "plateau", "progression"]):
        return "strength"
    if any(w in q for w in ["volume", "sets", "growth", "enough"]):
        return "volume"
    if any(w in q for w in ["session", "frequency", "how often", "last week", "recent"]):
        return "sessions"
    if any(w in q for w in ["last workout", "last session", "yesterday", 
                         "what did i do", "exercises", "sets i did"]):
        return "raw_sets"
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