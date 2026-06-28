from src.config import OPENAI_API_KEY, CHROMA_PATH, EMBEDDING_MODEL
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from src.db.snowflake_client import get_conn
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma

GOLD_SCHEMA_CONTEXT = """
You have access to these Snowflake gold tables:

1. HEVY.GOLD.WEEKLY_VOLUME
   - week_start, gym, gym_detail, primary_muscle, equipment_category
   - total_sets, total_reps, total_tonnage, avg_weight_kg, max_weight_kg
   - tonnage_pct_change (week over week %)
   Use for: volume trends, muscle group analysis, progressive overload signals
   primary_muscle values: "full_body","traps","glutes","biceps","forearms","hamstrings","chest","upper_back","shoulders","quadriceps"

2. HEVY.GOLD.PR_TRACKING
   - exercise_title (format: "Exercise Name (Equipment)", e.g. "Bent Over Row (Barbell)")
   - gym_detail (lowercase: 'thrive', 'vr_fitness', 'cult_afterburn', 'nitw_gym', 'cult_chennai')
   - workout_date, max_weight_kg, cumulative_pr_kg, is_pr, is_cross_gym_comparable
   Use for: personal records, strength progression over time
   TIP: Use LIKE or ILIKE for exercise name matching, e.g. WHERE LOWER(exercise_title) LIKE '%bent%row%'
   primary_muscle values: "quadriceps","biceps","abdominals","abductors","traps","glutes","chest","lats","calves","triceps"

3. HEVY.GOLD.FATIGUE_INDEX
   - workout_date, gym, gym_detail
   - daily_tonnage, rolling_7d_avg_tonnage, rolling_28d_avg_tonnage
   - fatigue_index, training_status (optimal/overreaching/undertraining)
   Use for: recovery status, deload decisions, training load management

4. HEVY.SILVER.STG_SESSIONS
   - workout_title, workout_date, gym, gym_detail
   - session_duration_min, total_sets, total_exercises, total_tonnage
   Use for: session frequency, training consistency

Rules:
- Always filter by gym_detail when comparing PRs across sessions (use exact lowercase gym names)
- For exercise name matching, use LIKE '%exercise%' to find variations
- fatigue_index > 1.2 = overreaching, < 0.8 = undertraining
- Return only columns relevant to the question

gym_detail values: 'thrive', 'cult_chennai', 'cult_afterburn', 
   'vr_fitness', 'nitw_gym', 'blr_other', 'pune_other'

IMPORTANT DEFAULTS — always apply unless user specifies otherwise:
- For fatigue/overtraining questions: filter last 30 days
  WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
- For PR questions: show all time, ORDER BY workout_date DESC
- For volume questions: filter last 8 weeks
  WHERE week_start >= DATEADD('week', -8, CURRENT_DATE)
- For progression questions: filter last 4 weeks
  WHERE workout_date >= DATEADD('day', -28, CURRENT_DATE)
  
  If you want to keep the prompt approach, make the instructions much more explicit and forceful:
pythonIMPORTANT DEFAULTS — YOU MUST FOLLOW THESE, NO EXCEPTIONS:

1. FATIGUE/OVERTRAINING/DELOAD questions:
   ALWAYS add: WHERE workout_date >= DATEADD('day', -30, CURRENT_DATE)
   NEVER query dates before this window

2. VOLUME questions:
   ALWAYS add: WHERE week_start >= DATEADD('week', -8, CURRENT_DATE)
   NEVER return all-time totals

3. PROGRESSION questions:
   ALWAYS add: WHERE workout_date >= DATEADD('day', -28, CURRENT_DATE)

4. PR questions:
   No date filter needed — show all time

BEFORE writing the SQL, identify which category the question falls into
and apply the corresponding date filter. This is mandatory.
"""

def snowflake_tool(question: str) -> dict:
    """
    Converts a natural language question into SQL,
    runs it against gold tables, returns results.
    """

    llm = ChatOpenAI(
        model_name="gpt-4o-mini",
        openai_api_key=OPENAI_API_KEY,
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
    print(f"Generated SQL:\n{sql}\n")
    try:
        conn   = get_conn()
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows    = cursor.fetchall()
        cursor.close()
        conn.close()

        results = [dict(zip(columns, row)) for row in rows]
        return {
            "sql":     sql,
            "results": results,
            "error":   None,
        }

    except Exception as e:
        return {
            "sql":     sql,
            "results": [],
            "error":   str(e),
        }
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