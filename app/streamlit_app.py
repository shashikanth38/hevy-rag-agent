# app/streamlit_app.py
import streamlit as st
import sys
from pathlib import Path

# ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.graph import ask

# ── page config ───────────────────────────────────────────────
st.set_page_config(
    page_title = "Hevy Intelligence",
    page_icon  = "🏋️",
    layout     = "wide",
)

# ── custom CSS — dark premium theme ──────────────────────────
st.markdown("""
<style>
    .stApp { background-color: #0D0D1A; }
    
    .metric-card {
        background-color: #12121F;
        border: 1px solid #2A2A3E;
        border-radius: 12px;
        padding: 20px;
        margin: 8px 0;
    }
    .metric-label {
        color: #7B7B9E;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 1.5px;
        text-transform: uppercase;
        margin-bottom: 8px;
    }
    .metric-value { color: #E8E8F0; font-size: 15px; line-height: 1.6; }
    .section-header {
        color: #7B7B9E;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 1.5px;
        text-transform: uppercase;
        margin: 16px 0 8px 0;
    }
    .badge-high   { background:#0D2E1A; color:#4ADE80; border:1px solid #166534; border-radius:6px; padding:2px 10px; font-size:12px; }
    .badge-medium { background:#2A1F0A; color:#FB923C; border:1px solid #92400E; border-radius:6px; padding:2px 10px; font-size:12px; }
    .badge-low    { background:#2A0A0A; color:#F87171; border:1px solid #991B1B; border-radius:6px; padding:2px 10px; font-size:12px; }
    .citation { background:#12121F; border:1px solid #2A2A3E; border-radius:8px; padding:8px 12px; margin:4px 0; font-size:13px; color:#7B7B9E; }
    .citation a { color:#6366F1; text-decoration:none; }
    #MainMenu { visibility:hidden; }
    footer { visibility:hidden; }
</style>
""", unsafe_allow_html=True)

# ── sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🏋️ Hevy Intelligence")
    st.markdown(
        "<p style='color:#7B7B9E;font-size:13px;'>Your personal fitness AI — "
        "grounded in your actual training data and sports science research.</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    st.markdown(
        "<p class='section-header'>Suggested questions</p>",
        unsafe_allow_html=True,
    )

    suggestions = [
        "What is my bent over row PR at Thrive?",
        "Should I deload this week?",
        "Am I doing enough volume for leg growth?",
        "How has my squat progressed over the last month?",
        "What does the research say about training frequency?",
        "Am I overtraining?",
    ]

    for s in suggestions:
        if st.button(s, use_container_width=True, key=s):
            st.session_state.pending_question = s

    st.divider()
    st.markdown(
        "<p style='color:#3A3A5E;font-size:11px;'>Powered by Snowflake + ChromaDB + GPT-4o-mini</p>",
        unsafe_allow_html=True,
    )


# ── main area ─────────────────────────────────────────────────
st.markdown(
    "<h1 style='color:#E8E8F0;font-weight:300;letter-spacing:2px;'>HEVY INTELLIGENCE</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='color:#7B7B9E;margin-top:-12px;'>Ask anything about your training</p>",
    unsafe_allow_html=True,
)

# initialise session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None


def render_response(result: dict) -> None:
    """Render a structured agent response."""

    # recommendation
    st.markdown(
        "<p class='section-header'>Recommendation</p>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='metric-card'><div class='metric-value'>{result['recommendation']}</div></div>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            "<p class='section-header'>Your numbers</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='metric-card'><div class='metric-value'>{result['your_numbers']}</div></div>",
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            "<p class='section-header'>Research evidence</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='metric-card'><div class='metric-value'>{result['evidence']}</div></div>",
            unsafe_allow_html=True,
        )

    # action
    st.markdown(
        "<p class='section-header'>Action</p>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='metric-card'>"
        f"<div class='metric-label'>Next step</div>"
        f"<div class='metric-value'>→ {result['action']}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # confidence + citations
    conf  = result.get("confidence", "medium")
    badge = f"<span class='badge-{conf}'>{conf.upper()}</span>"
    st.markdown(
        f"<p class='section-header'>Confidence {badge}</p>",
        unsafe_allow_html=True,
    )

    if result.get("citations"):
        st.markdown(
            "<p class='section-header'>Citations</p>",
            unsafe_allow_html=True,
        )
        for c in result["citations"]:
            title  = c.get("title", "")[:80]
            source = c.get("source", "")
            st.markdown(
                f"<div class='citation'>📄 <a href='{source}' target='_blank'>{title}</a></div>",
                unsafe_allow_html=True,
            )


# render chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.write(msg["content"])
        else:
            render_response(msg["content"])


# handle sidebar button clicks
if st.session_state.pending_question:
    question = st.session_state.pending_question
    st.session_state.pending_question = None

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = ask(question)
        render_response(result)

    st.session_state.messages.append({"role": "assistant", "content": result})
    st.rerun()


# chat input
if question := st.chat_input("Ask about your training..."):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = ask(question)
        render_response(result)

    st.session_state.messages.append({"role": "assistant", "content": result})
    st.rerun()