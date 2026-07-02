"""Monitor agent - detects errors from Azure Blob Storage CSV files."""
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
