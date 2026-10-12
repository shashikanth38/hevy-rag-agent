# src/agent/state.py
from typing import TypedDict, Any


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

    # output
    answer:       str
    citations:    list[dict]  # list of dicts not str — each has title, source, pmid
    confidence:   str         # low / medium / high
    response:     Any         # AgentResponse Pydantic object from synthesis node

    # control
    tools_to_use: list[str]