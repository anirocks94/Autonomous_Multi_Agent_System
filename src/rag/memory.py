"""Unified RAG memory — singleton wrapping ChromaDB + SQLite.

Used by both main.py (terminal) and dashboard/app.py (Streamlit).
"""
import threading
from typing import Optional, List, Dict, Any
from persistence.sqlite_store import SQLiteStore
from persistence.chroma_store import ChromaStore


class DebugMemory:
    """Singleton that exposes RAG retrieval, outcome storage, and live events."""

    _instance: Optional["DebugMemory"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.sqlite = SQLiteStore()
        self.chroma = ChromaStore()

    @classmethod
    def get_instance(cls) -> "DebugMemory":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Core API ──────────────────────────────────────────────────────

    def store_outcome(self, state: Dict[str, Any]) -> None:
        """Save a completed workflow to both SQLite and ChromaDB."""
        self.sqlite.save_workflow(state)
        self.chroma.store_workflow_outcome(state)

    def retrieve_context(self, error_type: str, message: str,
                         top_k: Optional[int] = None) -> str:
        """Retrieve pattern-focused context of similar past fixes for agent prompts.

        Returns HOW similar errors have been fixed before (strategies, anti-patterns),
        NOT where they occurred (file paths are volatile and misleading).
        """
        query = f"Error pattern: {error_type}\nMessage: {message}"
        results = self.chroma.retrieve_similar(query, top_k=top_k)

        if not results:
            return ""

        lines = ["## Relevant Past Fix Patterns\n"]
        lines.append(
            "> These patterns come from previously resolved bugs of similar type.\n"
            "> Use the fix strategies as a starting point but READ THE ACTUAL CODE\n"
            "> before applying — file contents change, patterns don't.\n"
        )

        for i, entry in enumerate(results, 1):
            meta = entry["metadata"]
            similarity = 1 - entry["distance"] if entry.get("distance") is not None else None
            sim_str = f" (similarity: {similarity:.2f})" if similarity is not None else ""
            outcome_icon = "✅" if meta.get("status") in ("pr_created", "done") else "❌"

            lines.append(f"### Past Pattern {i}{sim_str} {outcome_icon}")
            lines.append(f"- **Error Category:** {meta.get('error_category', 'N/A')}")
            lines.append(f"- **Proven Fix Strategy:** `{meta.get('fix_pattern', 'N/A')}`")
            lines.append(f"- **How it was fixed:** {meta.get('fix_description', 'N/A')}")
            lines.append(f"- **Confidence when fixed:** {meta.get('confidence', 'N/A')}")
            lines.append(f"- **Build passed:** {meta.get('build_passed', 'N/A')}")

            failed = meta.get("failed_approaches", "")
            if failed:
                lines.append(f"- **⚠ FAILED approaches (do NOT repeat):** {failed}")

            lines.append("")

        return "\n".join(lines)


    def record_event(self, session_id: str, node_name: str,
                     data: Optional[Dict] = None) -> None:
        """Record a live node event (for dashboard progress)."""
        self.sqlite.record_event(session_id, node_name, data)

    # ── Dashboard helpers ─────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        stats = self.sqlite.get_stats()
        stats["rag_memories"] = self.chroma.count()
        return stats

    def list_workflows(self, status: Optional[str] = None,
                       limit: int = 50) -> List[Dict]:
        return self.sqlite.list_workflows(status=status, limit=limit)

    def get_workflow(self, session_id: str) -> Optional[Dict]:
        return self.sqlite.get_workflow(session_id)

    def get_events(self, session_id: str) -> List[Dict]:
        return self.sqlite.get_events(session_id)

    def search_memory(self, query: str, top_k: Optional[int] = None) -> List[Dict]:
        return self.chroma.retrieve_similar(query, top_k=top_k)

    def browse_memory(self, limit: int = 100) -> List[Dict]:
        return self.chroma.get_all_entries(limit=limit)

    def memory_count(self) -> int:
        return self.chroma.count()
