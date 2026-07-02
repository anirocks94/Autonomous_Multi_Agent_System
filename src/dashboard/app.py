"""Streamlit dashboard for the Autonomous C# Debugging Agent.

Run with:  streamlit run dashboard/app.py
"""
import sys
import os
import uuid

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from langgraph.types import Command

from config import Config
from nodes.monitor import MonitorAgent
from orchestrator import create_workflow
from rag.memory import DebugMemory

# ── Page Config ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="Autonomous Debug Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── One-time Initialisation ──────────────────────────────────────────


def _init():
    """Initialise singletons in session_state (runs once per browser tab)."""
    if "initialised" not in st.session_state:
        Config.validate()
        Config.setup_langsmith()
        st.session_state.workflow = create_workflow()
        st.session_state.monitor = MonitorAgent()
        st.session_state.memory = DebugMemory.get_instance()
        st.session_state.initialised = True

    # Per-workflow state
    for key, default in {
        "active_session_id": None,
        "thread_config": None,
        "pending_approval": False,
        "interrupt_value": None,
        "workflow_running": False,
        "workflow_done": False,
        "final_state": None,
        "log_lines": [],
    }.items():
        if key not in st.session_state:
            st.session_state[key] = default


_init()

# ── Sidebar ───────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🤖 Debug Agent")
    st.caption(f"Model: `{Config.AZURE_OPENAI_DEPLOYMENT}`")

    stats = st.session_state.memory.get_stats()
    c1, c2, c3 = st.columns(3)
    c1.metric("Workflows", stats["total_workflows"])
    c2.metric("Success", f"{stats['success_rate']:.0f}%")
    c3.metric("RAG", stats["rag_memories"])

    st.divider()

    if st.button("🔍 Check for Errors", use_container_width=True,
                 disabled=st.session_state.workflow_running):
        _trigger_monitor()

    if st.session_state.workflow_running:
        st.info("Workflow in progress…")
    elif st.session_state.workflow_done:
        st.success("Workflow complete")

# ── Helpers ───────────────────────────────────────────────────────────


def _log(msg: str):
    st.session_state.log_lines.append(msg)


def _trigger_monitor():
    """Check blob storage and kick off the workflow if an error is found."""
    initial_state = st.session_state.monitor.detect_errors()
    if initial_state is None:
        st.sidebar.warning("No new exception files found.")
        return

    session_id = initial_state["session_id"]
    thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    st.session_state.active_session_id = session_id
    st.session_state.thread_config = thread_config
    st.session_state.workflow_running = True
    st.session_state.workflow_done = False
    st.session_state.pending_approval = False
    st.session_state.interrupt_value = None
    st.session_state.final_state = None
    st.session_state.log_lines = []

    _log(f"🚨 Processing Error: {initial_state['error_event']['error_type']}")

    _run_workflow_stream(initial_state)


def _run_workflow_stream(input_data):
    """Stream workflow events until it pauses (interrupt) or finishes."""
    wf = st.session_state.workflow
    cfg = st.session_state.thread_config

    for event in wf.stream(input_data, config=cfg, stream_mode="updates"):
        for node_name, update in event.items():
            _log(f"  ✅ Node `{node_name}` completed")
            if isinstance(update, dict) and "status" in update:
                st.session_state.final_state = update

    # Check for interrupt
    snapshot = wf.get_state(cfg)
    if snapshot.next:
        for task in snapshot.tasks or []:
            if hasattr(task, "interrupts") and task.interrupts:
                st.session_state.pending_approval = True
                st.session_state.interrupt_value = task.interrupts[0].value
                _log("⏸️  Waiting for human approval")
                st.rerun()
                return

    # Workflow finished
    final_snapshot = wf.get_state(cfg)
    if final_snapshot.values:
        st.session_state.final_state = final_snapshot.values

    _finish_workflow()


def _resume_workflow(decision: dict):
    """Resume from interrupt with the human's decision."""
    st.session_state.pending_approval = False
    st.session_state.interrupt_value = None

    _log(f"  ▶️  Resumed with decision: {decision['decision']}")

    wf = st.session_state.workflow
    cfg = st.session_state.thread_config

    for event in wf.stream(Command(resume=decision), config=cfg, stream_mode="updates"):
        for node_name, update in event.items():
            _log(f"  ✅ Node `{node_name}` completed")
            if isinstance(update, dict) and "status" in update:
                st.session_state.final_state = update

    # Check for more interrupts
    snapshot = wf.get_state(cfg)
    if snapshot.next:
        for task in snapshot.tasks or []:
            if hasattr(task, "interrupts") and task.interrupts:
                st.session_state.pending_approval = True
                st.session_state.interrupt_value = task.interrupts[0].value
                _log("⏸️  Waiting for human approval (retry)")
                st.rerun()
                return

    final_snapshot = wf.get_state(cfg)
    if final_snapshot.values:
        st.session_state.final_state = final_snapshot.values

    _finish_workflow()


def _finish_workflow():
    """Mark workflow complete, persist to memory."""
    st.session_state.workflow_running = False
    st.session_state.workflow_done = True
    if st.session_state.final_state:
        st.session_state.memory.store_outcome(st.session_state.final_state)
        _log("📊 Workflow complete — outcome stored in RAG memory")
    st.rerun()


# ── Main Content ──────────────────────────────────────────────────────

st.header("Autonomous C# Debugging Agent — Stage 5")

# ── Approval Gate UI ──────────────────────────────────────────────────
if st.session_state.pending_approval and st.session_state.interrupt_value:
    iv = st.session_state.interrupt_value

    st.subheader("🔍 Fix Review — Human Approval Required")

    col_l, col_r = st.columns([2, 1])
    with col_l:
        st.markdown(f"""
| Field | Value |
|-------|-------|
| **Error Type** | `{iv.get('error_type', 'N/A')}` |
| **Message** | {iv.get('error_message', 'N/A')} |
| **File** | `{iv.get('file', 'N/A')}:{iv.get('line', 'N/A')}` |
| **Strategy** | {iv.get('strategy', 'N/A')} |
| **Confidence** | {iv.get('confidence', 'N/A')} |
| **Attempt** | {iv.get('attempt', 'N/A')} |
""")

    with col_r:
        with st.expander("Fixed Code Preview", expanded=True):
            st.code(iv.get("fixed_code_preview", "N/A"), language="csharp")

    feedback = st.text_area("Feedback (optional)", key="approval_feedback")

    btn_cols = st.columns(3)
    with btn_cols[0]:
        if st.button("✅ Approve", use_container_width=True, type="primary"):
            _resume_workflow({"decision": "approved", "feedback": feedback})
    with btn_cols[1]:
        if st.button("🔄 Retry", use_container_width=True):
            _resume_workflow({"decision": "changes_requested", "feedback": feedback})
    with btn_cols[2]:
        if st.button("❌ Reject", use_container_width=True):
            _resume_workflow({"decision": "rejected", "feedback": feedback})

# ── Workflow Summary ──────────────────────────────────────────────────
elif st.session_state.workflow_done and st.session_state.final_state:
    fs = st.session_state.final_state
    status = fs.get("status", "unknown")

    if status == "pr_created":
        st.success(f"PR Created: {fs.get('pr_url', 'N/A')}")
    elif status == "failed":
        st.error(f"Failed: {fs.get('failure_reason', 'Unknown')}")
    elif status == "rejected":
        st.warning("Fix rejected by reviewer")
    elif status == "escalated":
        esc = fs.get("escalation")
        if esc:
            st.warning(f"Escalated: Work item #{esc.get('work_item_id', 'N/A')}")

    inv = fs.get("investigation_output")
    if inv:
        st.subheader("Investigation")
        st.markdown(f"**Root cause:** {inv['root_cause']}")
        st.markdown(f"**File:** `{inv['file_path']}:{inv['line_number']}`")
        st.markdown(f"**Strategy:** {inv['fix_strategy']} — Confidence: {inv['confidence']:.2f}")

    fix = fs.get("fix_output")
    if fix:
        st.subheader("Fix")
        st.markdown(f"**Strategy:** {fix['strategy_used']}")
        st.markdown(f"**Description:** {fix['fix_description']}")
        build_icon = "✅" if fix["build_passed"] else "❌"
        st.markdown(f"**Build:** {build_icon}  |  **Agent attempts:** {fix['attempts_made']}")
        with st.expander("Fixed Code"):
            st.code(fix.get("final_code", ""), language="csharp")

    # Decision trail
    decisions = fs.get("decisions", [])
    if decisions:
        st.subheader("Decision Trail")
        for d in decisions:
            st.markdown(f"- **{d['agent']}**: {d['reasoning']}")

    # Supervisor decisions
    sup = fs.get("supervisor_decisions", [])
    if sup:
        st.subheader("Supervisor Routing")
        for sd in sup:
            llm = " [LLM]" if sd.get("used_llm") else ""
            st.markdown(f"- 🧭 `{sd['decision_point']}`{llm}: **{sd['chosen_route']}** — {sd['reasoning']}")

# ── Idle state ────────────────────────────────────────────────────────
else:
    if not st.session_state.workflow_running:
        st.info("Click **Check for Errors** in the sidebar to start a workflow.")

# ── Event Log ─────────────────────────────────────────────────────────
if st.session_state.log_lines:
    with st.expander("Event Log", expanded=False):
        for line in st.session_state.log_lines:
            st.text(line)
