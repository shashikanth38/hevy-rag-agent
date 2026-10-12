from pydantic import BaseModel, Field
from typing import Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from src.agent.state import AgentState
from src.agent.tools import repair_sql, run_sql, snowflake_tool, chromadb_tool
from src.config import OPENAI_API_KEY

MAX_SQL_ATTEMPTS = 2   # first run + one repair

llm = ChatOpenAI(
        model_name="gpt-4o-mini",
        openai_api_key=OPENAI_API_KEY,
        temperature=0
)
class AgentResponse(BaseModel):
    recommendation: str = Field(
        description="Main recommendation or answer to the user's question"
    )
    your_numbers: str = Field(
        description="Relevant stats from the user's training data, or 'No data retrieved' if none"
    )
    evidence: str = Field(
        description="Supporting evidence from the numbered research papers, citing them "
                    "inline as [1], [2]. 'No papers retrieved' if none were provided, or "
                    "'The research corpus does not cover this' if none were relevant."
    )
    action: str = Field(
        description="One specific actionable next step for the user"
    )
    sources_used: list[int] = Field(
        default_factory=list,
        description="Numbers of the papers you actually drew on, e.g. [1, 3]. "
                    "Empty list if you used none of them.",
    )


def derive_confidence(state: AgentState) -> str:
    tools        = state.get("tools_to_use", [])
    sql_results  = state.get("sql_results") or {}
    paper_chunks = state.get("paper_chunks") or []
    notes        = state.get("data_notes") or []
    if "snowflake" in tools and (sql_results.get("error") or not sql_results.get("results")):
        return "low"
    if "chromadb" in tools and not paper_chunks:
        return "low"
    if notes:
        return "medium"
    return "high"

class SupervisorDecision(BaseModel):
    tools_to_use: list[str] = Field(
        description="List of tools to call, e.g., ['snowflake', 'chromadb']"
    )
    reasoning: str = Field(
        description="Reasoning behind the decision"
    )
def normalize_sql(sql: str) -> str:
    """Collapse whitespace and case so trivially-identical SQL compares equal."""
    return " ".join((sql or "").split()).lower()
def format_history(history: list[dict], last_n_messages: int = 6) -> str:
    """Turn [{'role':..., 'content':...}, ...] into plain text for a prompt."""
    recent = (history or [])[-last_n_messages:]
    return "\n".join(f"{h['role']}: {h['content']}" for h in recent)

class RewrittenQuestion(BaseModel):
    resolved_question: str = Field(
        description="The question rewritten to be self-contained and clear"
    )
    is_followup: bool = Field(
    description="True if the question is a follow-up to the previous one, False otherwise")
    
REWRITE_PROMPT = """You rewrite questions for a fitness training assistant. Every
question is about the user's own weight-training history or about sports science.
Named places are GYMS the user trains at (thrive, vr_fitness, cult_afterburn,
cult_chennai, nitw_gym, blr_other, pune_other) — never companies or anything else.

Rewrite the user's latest message as a standalone question that can be understood
with no prior context.

Rules:
- Carry the SUBJECT of the conversation forward: the exercise, muscle, metric or
  time window being discussed. "what about at Thrive?" is asking the SAME question
  about a different gym, so the exercise must appear in your rewrite.
- Resolve pronouns and ellipsis: "it", "that", "there", "what about X", "and for Y",
  "how about Z", "why".
- If the latest message introduces a genuinely new topic, do NOT carry anything
  over. Return it as its own standalone question.
- If the message is already standalone, return it unchanged.
- Set is_followup true whenever you used the conversation to resolve the message,
  false when the message stood on its own.
- Do not answer the question. Do not invent detail that is not in the conversation.

Examples (conversation → latest message → rewrite):

1. "How has my bench press progressed over the last month?"
   → "what about at Thrive?"
   → "How has my bench press progressed over the last month at Thrive?"  [followup]

2. "Am I doing enough volume for legs?"
   → "and for shoulders?"
   → "Am I doing enough volume for shoulders?"  [followup]

3. "What is my squat PR?"
   → "how about overhead press?"
   → "What is my overhead press PR?"  [followup]

4. "Should I deload this week?"
   → "why?"
   → "Why should I deload this week?"  [followup]

5. "What is my bench press PR?"
   → "what does the research say about sleep?"
   → "What does the research say about sleep?"  [NOT a followup — new topic,
      carry nothing over]

Conversation:
{history}"""

def contextualize(state: AgentState) -> AgentState:
    """
    Rewrites the user's question to be self-contained and clear.
    Updates the state with resolved_question and is_followup.
    """
    question = state["question"]
    history  = state.get("history") or []
    if not history:
        return {**state, "resolved_question": state["question"], "is_followup": False}
    try:
        structured_llm = llm.with_structured_output(RewrittenQuestion)
        prompt = ChatPromptTemplate.from_messages([
            ("system", REWRITE_PROMPT),
            ("human", "{question}"),
        ])
        chain = prompt | structured_llm
        result = chain.invoke({
            "history": format_history(history),
            "question": question
        })
        print(f"Rewritten question: {result.resolved_question}")
        print(f"Is follow-up: {result.is_followup}")

        return {
            **state,
            "resolved_question": result.resolved_question,
            "is_followup": result.is_followup
        }
    except Exception as e:
        print(f"Error rewriting question: {e}, using the original question itself")
        return {**state, "resolved_question": state["question"], "is_followup": False}

def supervisor(state: AgentState) -> AgentState:
    """
    Reads the question and decides which tools to call.
    Returns updated state with tools_to_use list.
    """
    structured_llm = llm.with_structured_output(SupervisorDecision)
    prompt = ChatPromptTemplate.from_messages([
        ("system", """
            You are a fitness AI supervisor. Given a user's question about their training,
            decide which tools are needed to answer it.

            Available tools:
            - snowflake: queries the user's actual workout data (PRs, volume, fatigue, sessions)
            - chromadb: retrieves sports science research papers

            Rules:
            - Use snowflake when the question asks about their personal data
            - Use chromadb when the question needs scientific backing or general advice
            - Use both when the question needs personal data AND scientific context
            - NEVER return an empty list. Every question gets at least one tool.
              If the question is unclear or seems off-topic, return ["chromadb"].

            Examples:
            Q: What is my bench press PR? → ["snowflake"]
            Q: How many sets per week for hypertrophy? → ["chromadb"]
            Q: Should I deload this week? → ["snowflake", "chromadb"]
            Q: Am I overtraining? → ["snowflake", "chromadb"]
            Q: What is progressive overload? → ["chromadb"]
        """),
        ("human", "{question}"),
    ])
    chain = prompt | structured_llm
    decision = chain.invoke({"question": state["resolved_question"]})

    # Hard guard: an empty list means both tool nodes skip and synthesis is left
    # with nothing to ground an answer in, which is how hallucinations get out.
    tools = [t for t in decision.tools_to_use if t in ("snowflake", "chromadb")]
    if not tools:
        print("Supervisor returned no usable tools — defaulting to ['chromadb'].")
        tools = ["chromadb"]

    print(f"Supervisor decision: {tools}")
    print(f"Reasoning: {decision.reasoning}")

    return {**state, "tools_to_use": tools}

def run_snowflake(state: AgentState) -> AgentState:
    """
    Calls the snowflake_tool with the question and updates the state with results.
    """
    if "snowflake" not in state.get("tools_to_use", []):
        print("Snowflake tool not selected by supervisor.")
        return state
    print("Running Snowflake tool...")
    result = snowflake_tool(state["resolved_question"])

    return {**state, "sql_results": result, "sql_attempts": state.get("sql_attempts", 0) + 1}

def repair_snowflake(state: AgentState) -> AgentState:
    """
    If the SQL query failed, attempts to repair it and re-run.
    Updates the state with new results and notes.
    """
    failed   = state["sql_results"]
    attempts = state.get("sql_attempts", 1)
    notes    = list(state.get("data_notes") or [])
    print(f"Repairing SQL (attempt {attempts + 1})...")
    new_sql = repair_sql(state["resolved_question"], failed["sql"], failed["error"])
    if normalize_sql(new_sql) == normalize_sql(failed["sql"]):
        print("Repair returned the same SQL — giving up.")
        notes.append("The data query failed and could not be repaired.")
        return {**state, "sql_attempts": MAX_SQL_ATTEMPTS, "data_notes": notes}

    result = run_sql(new_sql)
    if result["error"]:
        print(f"Repaired SQL also failed: {result['error']}")
        notes.append("The data query failed and the repair attempt also failed.")
    else:
        print(f"Repair worked — {len(result['results'])} rows.")
        notes.append("The first data query had an error; a corrected query was used.")

    return {**state, "sql_results": result, "sql_attempts": attempts + 1, "data_notes": notes}

def run_chromadb(state: AgentState) -> AgentState:
    """Calls chromadb_tool and stores results in state."""
    if "chromadb" not in state.get("tools_to_use", []):
        return state

    print("Running ChromaDB tool...")
    chunks = chromadb_tool(state["resolved_question"])

    return {**state, "paper_chunks": chunks}

# ── router: after snowflake / repair ──────────────────────────
def check_sql(state: AgentState) -> str:
    """
    Plain Python, no LLM. Decides from state whether the SQL needs repair.
    """
    if "snowflake" not in state.get("tools_to_use", []):
        return "continue"

    sql_results = state.get("sql_results") or {}
    if sql_results.get("error") and state.get("sql_attempts", 0) < MAX_SQL_ATTEMPTS:
        return "repair"

    return "continue"
def select_citations(sources_used: list, paper_chunks: list[dict]) -> list[dict]:
    """
    Keep only whole numbers in 1..len(paper_chunks), remove duplicates, sort,
    then look up the real paper. Each citation keeps its original number.
    """
    n = len(paper_chunks)
    valid = sorted({
        i for i in (sources_used or [])
        if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= n
    })
    return [
        {
            "index":  i,
            "title":  paper_chunks[i - 1]["title"],
            "source": paper_chunks[i - 1]["source"],
            "pmid":   paper_chunks[i - 1]["pmid"],
        }
        for i in valid
    ]


def synthesis(state: AgentState) -> AgentState:
    """
    Combines sql_results + paper_chunks into a
    structured, cited recommendation.
    """
    sql_results  = state.get("sql_results") or {}
    paper_chunks = state.get("paper_chunks", [])
    data_notes   = state.get("data_notes") or []
    asked_for_data = "snowflake" in state.get("tools_to_use", [])

    notes_line = f"\nRetrieval notes: {' '.join(data_notes)}" if data_notes else ""

    # Three cases, deliberately distinct: a failed query is NOT the same thing
    # as the user having no data, and the answer must not conflate them.
    if sql_results.get("results"):
        data_context = f"""
User's training data:
SQL: {sql_results.get('sql', '')}
Results: {sql_results.get('results')}{notes_line}
        """
    elif sql_results.get("error"):
        data_context = f"""
The query against the user's training data FAILED. No numbers are available.
This is a system error — it is NOT evidence that the user has no training data.
Do not say the user has no data, and do not invent numbers. Say the data could
not be retrieved. If research papers are provided below, answer from those alone.
Error: {sql_results.get('error')}{notes_line}
        """
    elif asked_for_data:
        data_context = f"""
The query ran successfully but returned no rows, so the user genuinely has no
matching data in the window queried. Say so explicitly.
SQL: {sql_results.get('sql', '')}{notes_line}
        """
    else:
        data_context = "No personal training data was needed for this question."

    # Nothing at all was retrieved. Without this the model answers from its own
    # knowledge and reports it as if it were grounded — test 3b produced a
    # confident paragraph about a gym being "stable" from an empty state.
    # A query that ran cleanly and returned zero rows IS a finding ("you have not
    # trained shoulders in 8 weeks"), so it does not count as nothing retrieved.
    sql_ran_clean = bool(sql_results) and sql_results.get("error") is None \
                    and sql_results.get("results") is not None
    nothing_retrieved = (not paper_chunks
                         and not sql_results.get("results")
                         and not sql_ran_clean)
    if nothing_retrieved:
        data_context += """

NOTHING WAS RETRIEVED — no training data and no research papers.
You have no grounds to answer. Do not answer from your own knowledge.
Reply that you could not retrieve anything for this question, and ask the user to
rephrase it in terms of their training."""

    if paper_chunks:
        papers_context = "\n\n".join([
            f"[{i}] {p['title']}\n{p['content'][:500]}"
            for i, p in enumerate(paper_chunks, 1)
        ])
    else:
        papers_context = "No research papers retrieved."

    structured_llm = llm.with_structured_output(AgentResponse)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """
You are a knowledgeable fitness coach and data analyst.
Answer the user's question using their personal training data and/or research papers.

Guidelines:
- Be specific and reference actual numbers from their data
- Research papers are NUMBERED. Cite them inline by number in `evidence`,
  e.g. "higher frequency improved hypertrophy [2]".
- List in `sources_used` ONLY the numbers of papers whose content you actually
  used. If you used none of them, return an empty list.
- State a research finding only if it appears in the papers below. If they do not
  address the question, say the research corpus does not cover it — never answer
  from your own general knowledge.
- Be concise but complete
- If data is missing, say so clearly
- Distinguish "the query failed" from "the user has no data" — never report a
  retrieval failure as an absence of training history
- End with one actionable recommendation
        """),
        ("human", """
Question: {question}

{data_context}

Research papers:
{papers_context}
        """),
    ])

    chain    = prompt | structured_llm
    response = chain.invoke({
        "history":          format_history(state.get("history") or []),
        "question":        state["resolved_question"],
        "data_context":    data_context,
        "papers_context":  papers_context,
    })

    citations = select_citations(response.sources_used, paper_chunks)
    print(f"Papers: {len(paper_chunks)} retrieved · {len(citations)} cited "
          f"(model said {response.sources_used})")

    return {
        **state,
        "answer":     response.recommendation,
        "citations":  citations,
        "confidence": derive_confidence(state),
        "response":   response
    }
