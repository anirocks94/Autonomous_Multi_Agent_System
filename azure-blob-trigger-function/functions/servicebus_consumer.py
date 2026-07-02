"""
functions/servicebus_consumer.py — F2: Service Bus Trigger (Blueprint).

RESPONSIBILITY:
  Consume DebugState messages from the Service Bus queue and kick off
  the LangGraph agent workflow. Delegates ALL business logic to services/.

FLOW:
  Service Bus message → deserialise JSON → workflow_runner.run()
    LangGraph: clone → investigate → fix
      → (interrupt auto-approved) → create_pr
      → poll_reviews   [waits for Azure DevOps reviewer]
      → validate_feedback
        ├── approved          → END (PR merged ✅)
        ├── changes_requested → back to fix (retry)
        └── escalated         → escalate → END
"""
import json
import logging

import azure.functions as func

from services import workflow_runner

logger = logging.getLogger(__name__)
bp     = func.Blueprint()


@bp.service_bus_queue_trigger(
    arg_name="msg",
    queue_name="%SERVICE_BUS_QUEUE_NAME%",       # resolved from app settings at runtime
    connection="SERVICE_BUS_CONNECTION_STRING",
)
def debug_job_consumer(msg: func.ServiceBusMessage) -> None:
    """
    Triggered when a new DebugState message lands in the debug-jobs queue.

    Deserialises the JSON body and runs the full LangGraph workflow.
    The workflow auto-approves the interrupt to create a PR, and then
    Azure DevOps reviewers handle the actual code review via poll_reviews
    and validate_feedback nodes.
    """
    # ── Deserialise ──────────────────────────────────────────────────────────
    body = msg.get_body().decode("utf-8")
    try:
        initial_state = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.error("❌ [F2] Bad message body: %s", exc)
        return    # bad message → dead-letter, do not retry

    session_id = initial_state.get("session_id", "unknown")
    error_type = initial_state.get("error_event", {}).get("error_type", "unknown")
    logger.info("▶️  [F2] Consuming job: session=%s error=%s", session_id, error_type)

    # ── Run the full workflow ────────────────────────────────────────────────
    try:
        workflow_runner.run(initial_state)
        logger.info("✅ [F2] Workflow complete for session=%s", session_id)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("❌ [F2] Workflow error — session=%s: %s", session_id, exc)
        raise    # re-raise → Service Bus retries, then dead-letters after max retries
