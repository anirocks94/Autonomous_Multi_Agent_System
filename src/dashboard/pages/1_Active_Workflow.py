"""Active Workflow page — shows live progress of the current workflow."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import streamlit as st
from rag.memory import DebugMemory

st.set_page_config(page_title="Active Workflow", page_icon="🔬", layout="wide")
st.header("🔬 Active Workflow")

# ── Guard ──────────────────────────────────────────────────────────────
session_id = st.session_state.get("active_session_id")
if not session_id:
    st.info("No active workflow. Go to the main page and click **Check for Errors**.")
    st.stop()

memory = DebugMemory.get_instance()

# ── Node progress ─────────────────────────────────────────────────────
NODE_ORDER = ["clone_repo", "investigate", "fix", "approve", "create_pr",
              "poll_reviews", "validate_feedback", "escalate"]

events = memory.get_events(session_id)
completed_nodes = {e["node_name"] for e in events}

st.subheader("Node Progress")
cols = st.columns(len(NODE_ORDER))
for i, node in enumerate(NODE_ORDER):
    with cols[i]:
        if node in completed_nodes:
            st.success(node, icon="✅")
        else:
            st.info(node, icon="⬜")

# ── Investigation ─────────────────────────────────────────────────────
fs = st.session_state.get("final_state") or {}

inv = fs.get("investigation_output")
if inv:
    st.subheader("Investigation Results")
    c1, c2, c3 = st.columns(3)
    c1.metric("Confidence", f"{inv['confidence']:.2f}")
    c2.metric("Strategy", inv["fix_strategy"])
    c3.metric("Category", inv["error_category"])
    st.markdown(f"**Root cause:** {inv['root_cause']}")
    st.markdown(f"**File:** `{inv['file_path']}:{inv['line_number']}`")
    if inv.get("affected_files"):
        st.markdown(f"**Affected files:** {', '.join(inv['affected_files'])}")

# ── Fix ───────────────────────────────────────────────────────────────
fix = fs.get("fix_output")
if fix:
    st.subheader("Fix Results")
    c1, c2 = st.columns(2)
    c1.metric("Build", "PASSED" if fix["build_passed"] else "FAILED")
    c2.metric("Attempts", fix["attempts_made"])
    st.markdown(f"**Strategy:** {fix['strategy_used']}")
    st.markdown(f"**Description:** {fix['fix_description']}")
    with st.expander("Fixed Code"):
        st.code(fix.get("final_code", ""), language="csharp")

# ── Decision Trail ────────────────────────────────────────────────────
decisions = fs.get("decisions", [])
if decisions:
    st.subheader("Decision Trail")
    import pandas as pd
    df = pd.DataFrame([
        {"Agent": d["agent"], "Point": d["decision_point"],
         "Choice": d["choice"], "Reasoning": d["reasoning"]}
        for d in decisions
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)

# ── Event log ─────────────────────────────────────────────────────────
if events:
    st.subheader("Raw Events")
    with st.expander("Show events"):
        for e in events:
            st.json(e)
