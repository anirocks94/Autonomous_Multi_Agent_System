"""Workflow History page — browse past workflow runs."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import streamlit as st
import pandas as pd
from rag.memory import DebugMemory

st.set_page_config(page_title="Workflow History", page_icon="📜", layout="wide")
st.header("📜 Workflow History")

memory = DebugMemory.get_instance()

# ── Filters ───────────────────────────────────────────────────────────
status_filter = st.selectbox(
    "Filter by status",
    ["All", "pr_created", "failed", "rejected", "escalated"],
)

status_arg = None if status_filter == "All" else status_filter
workflows = memory.list_workflows(status=status_arg, limit=100)

if not workflows:
    st.info("No workflows recorded yet.")
    st.stop()

# ── Table ─────────────────────────────────────────────────────────────
rows = []
for w in workflows:
    rows.append({
        "Session": w["session_id"],
        "Error Type": w.get("error_type", ""),
        "Status": w.get("status", ""),
        "Confidence": w.get("confidence", 0),
        "Strategy": w.get("strategy", ""),
        "File": w.get("file_path", ""),
        "PR URL": w.get("pr_url") or "",
        "Updated": w.get("updated_at", ""),
    })

df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True, hide_index=True)

# ── Expandable Details ────────────────────────────────────────────────
st.subheader("Details")
selected = st.selectbox("Select a session", [w["session_id"] for w in workflows])
if selected:
    wf = memory.get_workflow(selected)
    if wf:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Investigation:**")
            if wf.get("investigation_json"):
                st.json(json.loads(wf["investigation_json"]))
            else:
                st.caption("N/A")
        with col2:
            st.markdown("**Fix:**")
            if wf.get("fix_json"):
                st.json(json.loads(wf["fix_json"]))
            else:
                st.caption("N/A")
        if wf.get("pr_url"):
            st.markdown(f"**PR:** [{wf['pr_url']}]({wf['pr_url']})")
