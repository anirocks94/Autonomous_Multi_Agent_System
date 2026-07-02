"""
services/state_builder.py — Build the initial DebugState dict and publish it
to Azure Service Bus so downstream consumers can pick up the job.

WHAT THIS DOES:
  Constructs the same DebugState dict that MonitorAgent.detect_errors()
  historically returned, serialises it to JSON, and sends it to the
  Azure Service Bus queue so Function 2 (debug_job_consumer) picks it up.

LOCAL_DEV MODE:
  Set LOCAL_DEV=true in local.settings.json to skip the Service Bus call and
  print the state JSON to stdout (useful when no Azure infrastructure available).

ENV VARS:
  SERVICE_BUS_CONNECTION_STRING  — Azure Service Bus namespace connection string
  SERVICE_BUS_QUEUE_NAME         — Queue name (default: "debug-jobs")
  LOCAL_DEV                      — "true" to skip Service Bus (default: "false")
  MAX_ATTEMPTS                   — Max fixer retries (default: "3")
  MAX_REVIEW_POLLS               — Max PR review poll cycles (default: "20")
"""
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

_QUEUE_NAME = os.environ.get("SERVICE_BUS_QUEUE_NAME", "debug-jobs")
_LOCAL_DEV  = os.environ.get("LOCAL_DEV", "false").lower() == "true"


def build_and_publish(error_data: Dict) -> str:
    """
    Construct a DebugState dict from parsed error_data and publish it
    to Azure Service Bus (or stdout in LOCAL_DEV mode).

    Args:
        error_data: Normalised dict from services.csv_parser.parse().

    Returns:
        session_id string assigned to this debugging session.
    """
    session_id = str(uuid.uuid4())[:8]

    # ── ErrorEvent ──────────────────────────────────────────────────────────
    error_event = {
        "error_id":    error_data["problemId"],
        "error_type":  error_data["type"],
        "message":     error_data["sample_message"],
        "stack_trace": error_data["sample_stack"],
        "timestamp":   datetime.utcnow().isoformat(),
        "frequency":   error_data["count"],
    }

    # ── Full DebugState — identical to MonitorAgent.detect_errors() ─────────
    state = {
        "session_id":              session_id,
        "error_event":             error_event,
        "repo_path":               None,
        "branch_name":             None,
        "code_context":            None,
        "error_category":          None,
        "fix_strategy":            None,
        "confidence":              0.0,
        "analysis_result":         None,
        "parallel_strategies":     [],
        "fix_attempts":            [],
        "current_attempt":         1,
        "max_attempts":            int(os.environ.get("MAX_ATTEMPTS", "3")),
        "parallel_fix_attempts":   [],
        "best_fix_index":          None,
        "build_errors":            [],
        "test_results":            None,
        "approval":                None,
        "review_comments":         [],
        "parsed_feedback":         None,
        "review_poll_count":       0,
        "max_review_polls":        int(os.environ.get("MAX_REVIEW_POLLS", "20")),
        "reviewer_feedback_context": None,
        "escalation":              None,
        "supervisor_decisions":    [],
        "investigation_output":    None,
        "fix_output":              None,
        "rag_context":             None,
        "pr_url":                  None,
        "pr_number":               None,
        "decisions": [
            {
                "agent":          "blob_trigger_function",
                "decision_point": "error_detected",
                "choice":         "actionable",
                "reasoning": (
                    f"CSV '{error_data.get('source_file', 'unknown')}' "
                    f"with {error_data.get('total_errors', 1)} error(s) — "
                    "triggered via Azure Blob Storage event"
                ),
                "timestamp": datetime.utcnow().isoformat(),
            }
        ],
        "status":         "detecting",
        "failure_reason": None,
    }

    message_body = json.dumps(state, default=str)

    if _LOCAL_DEV:
        logger.info("LOCAL_DEV — printing DebugState to stdout instead of Service Bus")
        print("\n📨 DebugState (LOCAL_DEV — would be sent to Service Bus):")
        print(json.dumps(state, indent=2, default=str))
    else:
        _send_to_service_bus(message_body, session_id)

    return session_id


def _send_to_service_bus(message_body: str, session_id: str) -> None:
    """Enqueue a single JSON message on the Azure Service Bus queue."""
    from azure.servicebus import ServiceBusClient, ServiceBusMessage

    conn_str = os.environ["SERVICE_BUS_CONNECTION_STRING"]
    with ServiceBusClient.from_connection_string(conn_str) as client:
        with client.get_queue_sender(_QUEUE_NAME) as sender:
            msg = ServiceBusMessage(
                body=message_body,
                subject="debug-job",
                application_properties={"session_id": session_id},
            )
            sender.send_messages(msg)
            logger.info(
                "📨 Published to Service Bus queue '%s' (session_id=%s)",
                _QUEUE_NAME, session_id
            )
