# src/agent/state.py
from typing import TypedDict, Any

from pydantic import Field


class AgentState(TypedDict):
    # input
    question:     str
    history:       list[dict]  # list of dicts not str 
    resolved_question: str
    is_followup:   bool        # True if the question is a follow-up to the previous one
    
    # retrieved data
    sql_results:  dict        # dict not str — stores sql + results + error
    sql_attempts: int        # number of attempts made to run the SQL query
    data_notes:  list[str]  # notes about the SQL query after execution
    paper_chunks: list[dict]  # list of dicts not str — each has title, content, source, pmid
    sources_used: list[int] = Field(
        description="The numbers of the papers you actually drew on, e.g. [1, 3]. "
                    "Empty list if you used none."
        )  # list of sources used to answer the question (e.g., ["snowflake", "papers"])
    # output
    answer:       str
    citations:    list[dict]  # list of dicts not str — each has title, source, pmid
    confidence:   str         # low / medium / high
    response:     Any         # AgentResponse Pydantic object from synthesis node

    # control
    tools_to_use: list[str]