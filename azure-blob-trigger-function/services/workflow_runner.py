"""
services/workflow_runner.py — Bridge between Azure Functions and the LangGraph workflow.

WHY THIS SERVICE EXISTS:
  The LangGraph workflow lives in src/workflow.py and src/agents/*.
  Azure Functions run from the azure-blob-trigger-function/ directory, so
  this service adds src/ to sys.path before any project imports happen.

APPROVAL STRATEGY:
  The original main.py (terminal) uses LangGraph interrupt() to pause and
  wait for a human in the terminal to type approve/reject/retry.

  In the Azure Functions path, there is NO separate approval step.
  When the graph hits interrupt() at the approve node, this service
  auto-resumes with {"decision": "approved"} so the graph immediately
  proceeds to create_pr.

  The REAL human review happens in AZURE DEVOPS:
    - create_pr node  → opens the PR for review
    - poll_reviews    → polls Azure DevOps until the reviewer responds
    - validate_feedback → processes the review decision:
        approved          → done (PR merged)
        changes_requested → back to fix (retry loop)
        rejected          → escalate

  This is a cleaner separation: Azure Functions handles automation,
  Azure DevOps handles the code review workflow it was built for.
"""
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── Inject src/ into sys.path ────────────────────────────────────────────────
_THIS_DIR = Path(__file__).resolve().parent.parent   # azure-blob-trigger-function/
_SRC_DIR  = _THIS_DIR.parent / "src"                 # autonomous-debug-agent/src/

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ── Project imports (only safe after sys.path set) ────────────────────────────
from orchestrator import create_workflow          # noqa: E402
from rag.memory import DebugMemory           # noqa: E402
from langgraph.types import Command          # noqa: E402

# Compiled workflow singleton — reused across warm invocations (cold start once)
_workflow = None


def _get_workflow():
    global _workflow
    if _workflow is None:
        logger.info("Compiling LangGraph workflow (cold start)")
        _workflow = create_workflow()
    return _workflow


# ── Public API ────────────────────────────────────────────────────────────────

def run(initial_state: Dict) -> str:
    """
    Run the LangGraph workflow to completion.

    If the workflow pauses at the approve node interrupt(), this service
    auto-resumes with 'approved' so the graph proceeds to create_pr.
    The actual code review is then handled by Azure DevOps reviewers via
    the poll_reviews and validate_feedback nodes.

    Args:
        initial_state: Full DebugState dict from the Service Bus message.

    Returns:
        session_id string.
    """
    session_id = initial_state["session_id"]
    thread_id  = str(uuid.uuid4())
    cfg        = {"configurable": {"thread_id": thread_id}}
    workflow   = _get_workflow()

    logger.info("▶️  Workflow start — session=%s", session_id)

    # ── First pass: run until interrupt or END ────────────────────────────────
    _stream(workflow, initial_state, cfg)

    snapshot = workflow.get_state(cfg)

    # ── Auto-approve the interrupt if the graph paused ────────────────────────
    if snapshot.next:
        logger.info(
            "⏸️  Workflow paused at interrupt — auto-approving to create PR "
            "(Azure DevOps reviewers handle the actual review)"
        )
        _stream(workflow, Command(resume={"decision": "approved", "feedback": ""}), cfg)

    # ── Store outcome in RAG memory ───────────────────────────────────────────
    final = workflow.get_state(cfg).values or {}
    _store_outcome(final)

    logger.info(
        "✅ Workflow complete — session=%s status=%s",
        session_id, final.get("status", "unknown")
    )
    return session_id


# ── Internal helpers ──────────────────────────────────────────────────────────

def _stream(workflow, input_, cfg: dict) -> None:
    """Stream workflow events, logging each completed node."""
    for event in workflow.stream(input_, config=cfg, stream_mode="updates"):
        for node_name, update in event.items():
            logger.info("   ✔ node: %s  status=%s",
                        node_name, update.get("status", "—")
                        if isinstance(update, dict) else "—")


def _store_outcome(final_state: Dict) -> None:
    """Persist the workflow result to RAG memory (ChromaDB + SQLite)."""
    if not final_state:
        return
    try:
        DebugMemory.get_instance().store_outcome(final_state)
        logger.info("💾 RAG outcome stored — status=%s", final_state.get("status"))
    except Exception as exc:   # pylint: disable=broad-except
        logger.warning("⚠️  Could not store RAG outcome: %s", exc)
