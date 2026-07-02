"""
ChromaDB vector store for RAG memory of past workflow outcomes.

STORAGE PHILOSOPHY — Pattern-Focused, Not Location-Focused:

  What changes quickly (DON'T store):
    - File paths, line numbers, class names  <-- stale after any refactor
    - Specific code snippets                 <-- wrong after a single edit

  What stays stable (DO store):
    - Fix patterns  (e.g. "use TryGetValue instead of direct dict access")
    - Failed approaches (e.g. "try-catch here swallowed a critical exception")
    - Error categories (NullRef always needs null guard, regardless of codebase)
    - Coding conventions (e.g. "team uses ArgumentGuard.NotNull() not manual if")

  This makes RAG context useful for months across a continuously changing codebase.
"""
from typing import Optional, List, Dict, Any
from config import Config


class ChromaStore:
    """Persistent ChromaDB collection for semantic search over past errors/fixes."""

    def __init__(self):
        import chromadb
        self._client = chromadb.PersistentClient(path=str(Config.CHROMA_DB_PATH))
        self._collection = self._client.get_or_create_collection(
            name=Config.RAG_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Store ─────────────────────────────────────────────────────────

    def store_workflow_outcome(self, state: Dict[str, Any]) -> None:
        """Store a completed workflow outcome using a pattern-focused schema.

        The stored document is a reusable knowledge narrative — it captures
        HOW to fix a class of error, not WHERE the error happened today.
        This ensures RAG context stays relevant even after the codebase evolves.
        """
        session_id = state.get("session_id", "unknown")
        error = state.get("error_event", {})
        inv = state.get("investigation_output") or {}
        fix = state.get("fix_output") or {}
        decisions = state.get("decisions", [])

        # ── Derive pattern fields ───────────────────────────────────────────
        error_type   = error.get("error_type", "")
        error_cat    = inv.get("error_category", "")
        root_cause   = inv.get("root_cause", "")
        fix_strategy = fix.get("strategy_used", inv.get("fix_strategy", ""))
        fix_desc     = fix.get("fix_description", "")
        additional   = inv.get("additional_context", "")
        build_passed = fix.get("build_passed", False)
        outcome      = state.get("status", "unknown")
        confidence   = float(inv.get("confidence", state.get("confidence", 0.0)))

        # Collect any failed strategies from the decision log
        failed_approaches = [
            d["choice"] for d in decisions
            if d.get("agent") == "fixer" and "failed" in d.get("reasoning", "").lower()
        ]

        # ── Build the searchable document ───────────────────────────────────
        # This is what ChromaDB embeds and searches over — optimised for pattern
        # retrieval, not location retrieval.
        document = "\n".join(filter(None, [
            f"Error pattern: {error_type}",
            f"Category: {error_cat}",
            f"Root cause pattern: {root_cause}",
            f"Successful fix strategy: {fix_strategy}",
            f"Fix approach: {fix_desc}",
            f"Additional patterns: {additional}" if additional else "",
            f"FAILED approaches (do NOT repeat): {', '.join(failed_approaches)}"
                if failed_approaches else "",
        ]))

        # ── Metadata — small scalar fields for filtering/display ────────────
        # Intentionally excludes file_path, line_number, class_name (volatile).
        metadata = {
            "session_id":       session_id,
            "error_type":       error_type,
            "error_category":   error_cat,
            "status":           outcome,
            "confidence":       confidence,
            "fix_pattern":      fix_strategy,
            "fix_description":  fix_desc[:500],
            "failed_approaches": ", ".join(failed_approaches),
            "build_passed":     str(build_passed),
        }

        # Upsert (idempotent by session_id)
        self._collection.upsert(
            ids=[session_id],
            documents=[document],
            metadatas=[metadata],
        )

    # ── Retrieve ──────────────────────────────────────────────────────

    def retrieve_similar(self, query: str, top_k: Optional[int] = None) -> List[Dict]:
        """Return top-k similar past workflow outcomes for a query string."""
        k = top_k or Config.RAG_TOP_K
        if self._collection.count() == 0:
            return []

        results = self._collection.query(
            query_texts=[query],
            n_results=min(k, self._collection.count()),
        )

        entries = []
        for i in range(len(results["ids"][0])):
            entries.append({
                "id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i] if results.get("distances") else None,
            })
        return entries

    # ── Browse / Stats ────────────────────────────────────────────────

    def get_all_entries(self, limit: int = 100) -> List[Dict]:
        """Return all stored entries (for dashboard browsing)."""
        if self._collection.count() == 0:
            return []
        result = self._collection.get(limit=limit, include=["documents", "metadatas"])
        entries = []
        for i in range(len(result["ids"])):
            entries.append({
                "id": result["ids"][i],
                "document": result["documents"][i],
                "metadata": result["metadatas"][i],
            })
        return entries

    def count(self) -> int:
        return self._collection.count()
