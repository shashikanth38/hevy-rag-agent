from langgraph.graph import StateGraph, START, END
from src.agent.state import AgentState
from src.agent.nodes import (
    supervisor,
    run_snowflake,
    run_chromadb,
    synthesis,
    should_retry,
)


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("supervisor",    supervisor)
    graph.add_node("snowflake",     run_snowflake)
    graph.add_node("chromadb",      run_chromadb)
    graph.add_node("synthesis",     synthesis)
    

    graph.add_edge(START, "supervisor")

    # supervisor → snowflake → chromadb → synthesis (sequential)
    graph.add_edge("supervisor", "snowflake")
    graph.add_edge("snowflake", "chromadb")
    graph.add_edge("chromadb", "synthesis")

    graph.add_conditional_edges(
        "synthesis",
        should_retry,
        {
            "end":   END,
            "retry": "supervisor",   # loops back for another attempt
        }
    )
    return graph.compile()

# convenience function for running the agent
def ask(question: str) -> dict:
    app = build_graph()

    initial_state = {
        "question":     question,
        "sql_results":  {},
        "paper_chunks": [],
        "answer":       "",
        "citations":    [],
        "confidence":   "",
        "retry_count":  0,
        "tools_to_use": [],
        "response":     None,
    }

    final_state = app.invoke(initial_state)

    return {
        "question":       final_state["question"],
        "recommendation": final_state["response"].recommendation,
        "your_numbers":   final_state["response"].your_numbers,
        "evidence":       final_state["response"].evidence,
        "action":         final_state["response"].action,
        "confidence":     final_state["confidence"],
        "citations":      final_state["citations"],
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