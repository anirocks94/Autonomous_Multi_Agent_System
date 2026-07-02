"""
function_app.py — Azure Functions v2 entry point (registration only).

THIS FILE'S ONLY JOB:
  1. Create the FunctionApp instance
  2. Import each Blueprint from functions/
  3. Register each Blueprint with app.register_functions()

NO business logic lives here.  All function logic is in functions/.
All service logic is in services/.

Project structure:
  function_app.py          ← YOU ARE HERE (thin entry point)
  functions/
    blob_trigger.py        ← F1: Blob Storage trigger (Blueprint)
    servicebus_consumer.py ← F2: Service Bus trigger  (Blueprint)
  services/
    csv_parser.py          ← parses CSV bytes into error_data dict
    actionability_filter.py← .NET exception allowlist gate
    state_builder.py       ← builds DebugState + publishes to Service Bus
    workflow_runner.py     ← LangGraph bridge: run & auto-approve interrupt

NOTE on human approval:
  Approval is handled entirely within Azure DevOps — the agent creates the PR
  automatically, and the poll_reviews + validate_feedback nodes observe the
  reviewer's decision (approved / changes requested / rejected).
  There is NO separate HTTP approval endpoint needed.
"""
import azure.functions as func

from functions.blob_trigger        import bp as blob_trigger_bp
from functions.servicebus_consumer import bp as servicebus_consumer_bp

app = func.FunctionApp()

app.register_functions(blob_trigger_bp)
app.register_functions(servicebus_consumer_bp)
