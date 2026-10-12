import html
import sys
import traceback
from pathlib import Path
import streamlit as st

# ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.agent.graph import ask, error_result

MAX_HISTORY_TURNS   = 3     # how many previous Q&A pairs the agent sees
MAX_ANSWER_CHARS    = 300   # past answers are cut to this length


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
    .interpreted { color:#7B7B9E; font-size:12px; font-style:italic; margin:-4px 0 8px 0; }
    .data-note   { color:#FB923C; font-size:12px; margin:2px 0; }
    .badge-high   { background:#0D2E1A; color:#4ADE80; border:1px solid #166534; border-radius:6px; padding:2px 10px; font-size:12px; }
    .badge-medium { background:#2A1F0A; color:#FB923C; border:1px solid #92400E; border-radius:6px; padding:2px 10px; font-size:12px; }
    .badge-low    { background:#2A0A0A; color:#F87171; border:1px solid #991B1B; border-radius:6px; padding:2px 10px; font-size:12px; }
    .citation { background:#12121F; border:1px solid #2A2A3E; border-radius:8px; padding:8px 12px; margin:4px 0; font-size:13px; color:#7B7B9E; }
    .citation a { color:#6366F1; text-decoration:none; }
    #MainMenu { visibility:hidden; }
    footer { visibility:hidden; }
</style>
""", unsafe_allow_html=True)


# ── session state ─────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None


# ── sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🏋️ Hevy Intelligence")
    st.markdown(
        "<p style='color:#7B7B9E;font-size:13px;'>Your personal fitness AI — "
        "grounded in your actual training data and sports science research.</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    st.markdown("<p class='section-header'>Suggested questions</p>", unsafe_allow_html=True)

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
    if st.button("Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.markdown(
        "<p style='color:#3A3A5E;font-size:11px;'>Powered by Snowflake + ChromaDB + GPT-4o-mini</p>",
        unsafe_allow_html=True,
    )


# ── header ────────────────────────────────────────────────────
st.markdown(
    "<h1 style='color:#E8E8F0;font-weight:300;letter-spacing:2px;'>HEVY INTELLIGENCE</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='color:#7B7B9E;margin-top:-12px;'>Ask anything about your training</p>",
    unsafe_allow_html=True,
)


# ── helpers ───────────────────────────────────────────────────
def esc(text) -> str:
    """Escape LLM text before putting it inside HTML."""
    return html.escape(str(text or ""))


def build_history(messages: list[dict]) -> list[dict]:
    """
    Turn the chat log into the compact history the agent expects.
    Failed turns are skipped so error messages don't confuse the rewriter.
    """
    results = [
        m["content"] for m in messages
        if m["role"] == "assistant" and not m["content"].get("is_error")
    ]
    history = []
    for r in results[-MAX_HISTORY_TURNS:]:
        history.append({"role": "user",      "content": r.get("resolved_question") or r.get("question", "")})
        history.append({"role": "assistant", "content": (r.get("recommendation") or "")[:MAX_ANSWER_CHARS]})
    return history


def render_response(result: dict) -> None:
    """Render a structured agent response."""

    # show how a follow-up was understood
    if result.get("is_followup"):
        st.markdown(
            f"<p class='interpreted'>Interpreted as: {esc(result.get('resolved_question'))}</p>",
            unsafe_allow_html=True,
        )

    st.markdown("<p class='section-header'>Recommendation</p>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='metric-card'><div class='metric-value'>{esc(result['recommendation'])}</div></div>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("<p class='section-header'>Your numbers</p>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='metric-card'><div class='metric-value'>{esc(result['your_numbers'])}</div></div>",
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown("<p class='section-header'>Research evidence</p>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='metric-card'><div class='metric-value'>{esc(result['evidence'])}</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown("<p class='section-header'>Action</p>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='metric-card'>"
        f"<div class='metric-label'>Next step</div>"
        f"<div class='metric-value'>→ {esc(result['action'])}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # confidence + data notes
    conf = result.get("confidence") or "medium"
    if conf not in ("low", "medium", "high"):
        conf = "medium"
    st.markdown(
        f"<p class='section-header'>Confidence <span class='badge-{conf}'>{conf.upper()}</span></p>",
        unsafe_allow_html=True,
    )
    for note in result.get("data_notes") or []:
        st.markdown(f"<p class='data-note'>⚠ {esc(note)}</p>", unsafe_allow_html=True)

    # citations
    if result.get("citations"):
        st.markdown("<p class='section-header'>Citations</p>", unsafe_allow_html=True)
        # The number is the one the answer text cites as [n], so it is shown
        # as-is and never renumbered — otherwise [2] in the prose would point
        # at a different paper than [2] in this list.
        for c in result["citations"]:
            label  = f"[{c['index']}] " if c.get("index") else ""
            title  = esc(c.get("title", "")[:80])
            source = esc(c.get("source", ""))
            st.markdown(
                f"<div class='citation'>📄 {esc(label)}"
                f"<a href='{source}' target='_blank'>{title}</a></div>",
                unsafe_allow_html=True,
            )

def handle_question(question: str) -> None:
    """One path for both sidebar buttons and the chat box."""
    history = build_history(st.session_state.messages)   # built BEFORE adding the new question

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                result = ask(question, history)
            except Exception as e:
                traceback.print_exc()                      # full details in the terminal
                result = error_result(question, f"Error: {type(e).__name__}: {e}")

    st.session_state.messages.append({"role": "assistant", "content": result})
    st.rerun()


# ── chat history ──────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.write(msg["content"])
        else:
            render_response(msg["content"])


# ── input: sidebar click or chat box ──────────────────────────
if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    handle_question(q)

if q := st.chat_input("Ask about your training..."):
    handle_question(q)