"""
Monitor Agent — Azure Blob Storage Exception Poller & Workflow Trigger.

WHAT THIS FILE DOES:
  The MonitorAgent is the entry point to the entire autonomous debugging
  pipeline.  It polls Azure Blob Storage for newly uploaded exception CSV
  files (exported from Application Insights or a custom telemetry pipeline),
  determines whether the detected error is "actionable" (i.e. the agent
  has a known fix strategy for the error type), and if so, constructs the
  initial DebugState that is fed into the LangGraph workflow.

DETECTION FLOW:
    BlobMonitor.check_for_new_files()
        │
        ├─ No CSV files → return None  (workflow not triggered)
        │
        ├─ CSV downloaded → BlobMonitor parses into normalized error_data dict
        │
        └─ _is_actionable(error_data)?
              │
              ├─ False → return None  (low-frequency / unknown error type)
              │
              └─ True  → construct ErrorEvent + DebugState → return state

ACTIONABILITY FILTER (_is_actionable):
  Only a known allowlist of .NET exception types are handled autonomously:
    KeyNotFoundException, NullReferenceException, IndexOutOfRangeException,
    DivideByZeroException, ArgumentNullException, FormatException,
    ParseException, InvalidOperationException, FileNotFoundException …
  Errors outside this list (e.g. OutOfMemoryException, custom exceptions)
  are skipped — the agent would not know how to fix them reliably, and a
  bad fix is worse than no fix.

INITIAL STATE CONSTRUCTION:
  The monitor is the ONLY place where a fresh DebugState is created.
  Every field is explicitly initialised (no implicit None spreading across
  the graph).  This guarantees that all downstream nodes can safely do
  state.get("key") without KeyError.

TEAMS NOTIFICATION:
  After creating the initial state, the workflow's clone_repo node calls
  TeamsNotifier.notify_exception_received() to alert the team immediately
  so humans are aware the agent has started working on the error.

INTERVIEW TALKING POINTS:
  - Polling Azure Blob Storage is a simple, robust trigger mechanism
    that decouples exception ingestion from the agent runtime.
  - Allowlist-based actionability filtering prevents the agent from
    attempting fixes it cannot safely generate — a key safety property
    for autonomous AI systems.
  - Creating DebugState here (not lazily inside nodes) makes the full
    state schema immediately visible and reviewable at workflow start.
"""
from datetime import datetime
from typing import Optional
from state import DebugState, ErrorEvent, Decision
from utils.blob_monitor import BlobMonitor
from config import Config
import uuid


class MonitorAgent:
    """Monitors Azure Blob Storage for exception CSV files."""

    def __init__(self):
        """Initialize monitor agent."""
        self.blob_monitor = BlobMonitor()

    def detect_errors(self) -> Optional[DebugState]:
        """Check blob storage for new exception files."""
        print("🔍 Monitor Agent: Checking blob storage for exception files...")

        error_data = self.blob_monitor.check_for_new_files()

        if not error_data:
            print("   No new exception files found.")
            return None

        if not self._is_actionable(error_data):
            print(f"   Error {error_data['problemId']} not actionable")
            return None

        error_event: ErrorEvent = {
            "error_id": error_data['problemId'],
            "error_type": error_data['type'],
            "message": error_data['sample_message'],
            "stack_trace": error_data['sample_stack'],
            "timestamp": error_data['last_seen'],
            "frequency": error_data['count']
        }

        session_id = str(uuid.uuid4())[:8]

        state: DebugState = {
            "session_id": session_id,
            "error_event": error_event,
            "repo_path": None,
            "branch_name": None,
            "code_context": None,
            "error_category": None,
            "fix_strategy": None,
            "confidence": 0.0,
            # Stage 2: AI analysis
            "analysis_result": None,
            "parallel_strategies": [],
            # Generation
            "fix_attempts": [],
            "current_attempt": 1,
            "max_attempts": Config.MAX_ATTEMPTS,
            # Stage 2: Parallel fixes
            "parallel_fix_attempts": [],
            "best_fix_index": None,
            # Stage 2: Build errors for self-correction
            "build_errors": [],
            # Testing
            "test_results": None,
            # Stage 2: Approval
            "approval": None,
            # Stage 3: Review feedback
            "review_comments": [],
            "parsed_feedback": None,
            "review_poll_count": 0,
            "max_review_polls": Config.MAX_REVIEW_POLLS,
            "reviewer_feedback_context": None,
            # Stage 3: Escalation
            "escalation": None,
            # Stage 3: Supervisor
            "supervisor_decisions": [],
            # Stage 4: Agentic outputs
            "investigation_output": None,
            "fix_output": None,
            # Stage 5: RAG context
            "rag_context": None,
            # Output
            "pr_url": None,
            "pr_number": None,
            # Tracking
            "decisions": [],
            "status": "detecting",
            "failure_reason": None
        }

        decision: Decision = {
            "agent": "monitor",
            "decision_point": "error_detected",
            "choice": "actionable",
            "reasoning": f"CSV file '{error_data.get('source_file', 'unknown')}' with {error_data.get('total_errors', 1)} error(s)",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        print(f"   ✅ Detected error: {error_event['error_type']}")
        print(f"   Source: {error_data.get('source_file', 'unknown')}")
        print(f"   Session ID: {session_id}")

        # Mark file as processed
        if error_data.get('source_file'):
            self.blob_monitor.mark_processed(error_data['source_file'])

        return state

    def _is_actionable(self, error_data: dict) -> bool:
        """Determine if error is actionable."""
        actionable_types = [
            "System.Collections.Generic.KeyNotFoundException",
            "System.NullReferenceException",
            "System.IndexOutOfRangeException",
            "System.DivideByZeroException",
            "System.ArgumentNullException",
            "System.FormatException",
            "System.InvalidOperationException",
            "System.ArgumentException",
            "System.IO.FileNotFoundException",
            "MimeKit.ParseException",
            "KeyNotFoundException",
            "NullReferenceException",
            "IndexOutOfRangeException",
            "DivideByZeroException",
            "ArgumentNullException",
            "FormatException",
            "ParseException",
            "InvalidOperationException"
        ]

        error_type = error_data.get('type', '')
        return any(t in error_type for t in actionable_types)
