"""RAG Memory page — search and browse the vector memory store."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import streamlit as st
from rag.memory import DebugMemory

st.set_page_config(page_title="RAG Memory", page_icon="🧠", layout="wide")
st.header("🧠 RAG Memory")

memory = DebugMemory.get_instance()

# ── Stats ─────────────────────────────────────────────────────────────
count = memory.memory_count()
st.metric("Stored Memories", count)

# ── Search ────────────────────────────────────────────────────────────
st.subheader("Search Similar Errors")
query = st.text_input("Enter error type or message to search")
top_k = st.slider("Top K results", 1, 10, 3)

if query:
    results = memory.search_memory(query, top_k=top_k)
    if not results:
        st.info("No similar entries found.")
    else:
        for i, entry in enumerate(results, 1):
            meta = entry["metadata"]
            distance = entry.get("distance")
            similarity = (1 - distance) if distance is not None else None
            sim_str = f" — similarity: {similarity:.2f}" if similarity is not None else ""

            with st.expander(f"Result {i}: {meta.get('error_type', 'N/A')}{sim_str}"):
                st.markdown(f"**Session:** {meta.get('session_id', 'N/A')}")
                st.markdown(f"**Category:** {meta.get('error_category', 'N/A')}")
                st.markdown(f"**File:** `{meta.get('file_path', 'N/A')}`")
                st.markdown(f"**Strategy:** {meta.get('strategy_used', 'N/A')}")
                st.markdown(f"**Fix:** {meta.get('fix_description', 'N/A')}")
                st.markdown(f"**Confidence:** {meta.get('confidence', 'N/A')}")
                st.markdown(f"**Status:** {meta.get('status', 'N/A')}")
                st.divider()
                st.text(entry.get("document", ""))

# ── Browse All ────────────────────────────────────────────────────────
st.subheader("Browse All Memories")
if count == 0:
    st.info("No memories stored yet. Run a workflow to populate.")
else:
    entries = memory.browse_memory(limit=50)
    for entry in entries:
        meta = entry["metadata"]
        with st.expander(f"{meta.get('error_type', 'N/A')} — {meta.get('session_id', '')}"):
            st.markdown(f"**Strategy:** {meta.get('strategy_used', 'N/A')}")
            st.markdown(f"**Fix:** {meta.get('fix_description', 'N/A')}")
            st.markdown(f"**Status:** {meta.get('status', 'N/A')}")
            st.text(entry.get("document", ""))
