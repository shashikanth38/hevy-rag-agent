from langgraph.graph import StateGraph, START, END
from src.agent.state import AgentState
from src.agent.nodes import (
    check_sql,
    contextualize,
    repair_snowflake,
    supervisor,
    run_snowflake,
    run_chromadb,
    synthesis
)


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("contextualize", contextualize)
    graph.add_node("supervisor",    supervisor)
    graph.add_node("snowflake",     run_snowflake)
    graph.add_node("repair_sql",    repair_snowflake)
    graph.add_node("chromadb",      run_chromadb)
    graph.add_node("synthesis",     synthesis)
    
    graph.add_edge(START, "contextualize")
    graph.add_edge("contextualize", "supervisor")

    # supervisor → snowflake → chromadb → synthesis (sequential)
    graph.add_edge("supervisor", "snowflake")
    graph.add_conditional_edges(
        "snowflake",
        check_sql,
        {"repair": "repair_sql", "continue": "chromadb"},
    )
    graph.add_conditional_edges(
        "repair_sql",
        check_sql,
        {"repair": "repair_sql", "continue": "chromadb"},
    )
    graph.add_edge("chromadb", "synthesis")

    graph.add_edge(
        "synthesis",
        END
    )
    return graph.compile()
def error_result(question: str, message: str, resolved_question: str = "") -> dict:
    """
    A result with the same keys as a normal answer,
    so the UI can render a failure without special cases.
    """
    return {
        "question":          question,
        "resolved_question": resolved_question or question,
        "is_followup":       False,
        "recommendation":    "Sorry — I couldn't answer that right now. Please try again.",
        "your_numbers":      "No data retrieved",
        "evidence":          "No papers retrieved",
        "action":            "Try asking again, or rephrase the question.",
        "confidence":        "low",
        "citations":         [],
        "data_notes":        [message],
        "is_error":          True,
    }
# convenience function for running the agent
def ask(question: str, history: list | None = None) -> dict:
    app = build_graph()

    initial_state = {
        "question":     question,
        "history":      history or [],
        "resolved_question": "",
        "is_followup":   False,
        "sql_results":       {},
        "sql_attempts":      0,
        "data_notes":        [],
        "sql_results":  {},
        "paper_chunks": [],
        "answer":       "",
        "citations":    [],
        "confidence":   "",
        "tools_to_use": [],
        "response":     None,
    }

    final_state = app.invoke(initial_state)
    response    = final_state.get("response")

    # guard: synthesis didn't produce a response
    if response is None:
        return error_result(
            question,
            "The agent finished without producing an answer.",
            final_state.get("resolved_question", ""),
        )

    return {
        "question":          final_state["question"],
        "resolved_question": final_state["resolved_question"],
        "is_followup":       final_state["is_followup"],
        "recommendation":    response.recommendation,
        "your_numbers":      response.your_numbers,
        "evidence":          response.evidence,
        "action":            response.action,
        "confidence":        final_state["confidence"],
        "citations":         final_state["citations"],
        "data_notes":        final_state["data_notes"],
        "is_error":          False,
    }
if __name__ == "__main__":
    import json

    questions = [
        "What is my bent over row PR at Thrive?",
        "Should I deload this week?",
        "Am I doing enough volume for leg growth?"
    ]

    for q in questions:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        print('='*60)
        result = ask(q)
        print(f"\nRecommendation: {result['recommendation']}")
        print(f"Your numbers:   {result['your_numbers']}")
        print(f"Evidence:       {result['evidence']}")
        print(f"Action:         {result['action']}")
        print(f"Confidence:     {result['confidence']}")
        if result['citations']:
            print(f"Citations:")
            for c in result['citations']:
                print(f"  - {c['title'][:60]} ({c['source']})")