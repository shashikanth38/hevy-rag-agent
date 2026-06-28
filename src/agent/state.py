# src/agent/state.py
from typing import TypedDict, Any


class AgentState(TypedDict):
    # input
    question:     str

    # retrieved data
    sql_results:  dict        # dict not str — stores sql + results + error
    paper_chunks: list[dict]  # list of dicts not str — each has title, content, source, pmid

    # output
    answer:       str
    citations:    list[dict]  # list of dicts not str — each has title, source, pmid
    confidence:   str         # low / medium / high
    response:     Any         # AgentResponse Pydantic object from synthesis node

    # control
    retry_count:  int
    tools_to_use: list[str]