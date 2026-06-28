from pydantic import BaseModel, Field
from typing import Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from src.agent.state import AgentState
from src.agent.tools import snowflake_tool, chromadb_tool
from src.config import OPENAI_API_KEY

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
        description="Supporting evidence from research papers, or 'No papers retrieved' if none"
    )
    action: str = Field(
        description="One specific actionable next step for the user"
    )
    confidence: str = Field(
        description="low, medium, or high based on data availability"
    )

class SupervisorDecision(BaseModel):
    tools_to_use: list[str] = Field(
        description="List of tools to call, e.g., ['snowflake', 'chromadb']"
    )
    reasoning: str = Field(
        description="Reasoning behind the decision"
    )

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
    decision = chain.invoke({"question": state["question"]})
    print(f"Supervisor decision: {decision.tools_to_use}")
    print(f"Reasoning: {decision.reasoning}")

    return {**state, "tools_to_use": decision.tools_to_use}

def run_snowflake(state: AgentState) -> AgentState:
    """
    Calls the snowflake_tool with the question and updates the state with results.
    """
    if "snowflake" not in state.get("tools_to_use", []):
        print("Snowflake tool not selected by supervisor.")
        return state
    print("Running Snowflake tool...")
    result = snowflake_tool(state["question"])

    return {**state, "sql_results": result}

def run_chromadb(state: AgentState) -> AgentState:
    """Calls chromadb_tool and stores results in state."""
    if "chromadb" not in state.get("tools_to_use", []):
        return state

    print("Running ChromaDB tool...")
    chunks = chromadb_tool(state["question"])

    return {**state, "paper_chunks": chunks}

def synthesis(state: AgentState) -> AgentState:
    """
    Combines sql_results + paper_chunks into a
    structured, cited recommendation.
    """
    sql_results  = state.get("sql_results", {})
    paper_chunks = state.get("paper_chunks", [])

    if sql_results and sql_results.get("results"):
        data_context = f"""
User's training data:
SQL: {sql_results.get('sql', '')}
Results: {sql_results.get('results', [])}
        """
    else:
        data_context = "No personal training data retrieved."

    if paper_chunks:
        papers_context = "\n\n".join([
            f"Paper: {p['title']}\n{p['content'][:500]}\nSource: {p['source']}"
            for p in paper_chunks
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
- Cite research papers when used
- Be concise but complete
- If data is missing, say so clearly
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
        "question":        state["question"],
        "data_context":    data_context,
        "papers_context":  papers_context,
    })

    citations = [
        {"title": p["title"], "source": p["source"], "pmid": p["pmid"]}
        for p in paper_chunks
    ]

    retry_count = state.get("retry_count", 0) + 1

    return {
        **state,
        "answer":     response.recommendation,
        "citations":  citations,
        "confidence": response.confidence,
        "response":   response,
        "retry_count": retry_count,
    }
    
def should_retry(state: AgentState) -> str:
    """
    Decides whether to retry or end.
    Retries if answer is low confidence and we haven't tried too many times.
    """
    if state.get("confidence") == "low" and state.get("retry_count", 0) < 2:
        print("Low confidence — retrying...")
        return "retry"
    return "end"