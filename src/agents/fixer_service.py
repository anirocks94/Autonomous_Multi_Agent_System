"""
Fixer Microservice — FastAPI wrapper around the Fixer ReAct agent.

Runs as a separate Kubernetes Deployment so it can be scaled independently
from the Investigator and the Streamlit orchestrator.

POST /fix
  Body: FixRequest (investigation_output, error_event, repo_path, rag_context,
                    reviewer_feedback_context)
  Returns: FixResponse (FixResult + metadata)

GET  /health   — Kubernetes liveness / readiness probe
GET  /metrics  — Exposes replica_id and call counter for observability

NOTE on resource profile:
  The Fixer is more CPU-bursty than the Investigator because it runs
  `dotnet build` inside the container (via run_build tool).  This drives
  the separate HPA thresholds:
    Investigator → scale on LLM token throughput proxy (memory 65%)
    Fixer        → scale on CPU burst from build execution (CPU 55%)
"""
import os
import time
import uuid
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn

from config import Config
from tools import fixer_tools
from agents.fixer import create_fixer, FixResult

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fixer-service")

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Fixer Agent Service",
    description="ReAct agent for autonomous C# fix generation and dotnet build validation",
    version="1.0.0",
)

# ── Agent (initialised once at startup) ──────────────────────────────
agent = None

@app.on_event("startup")
async def startup():
    global agent
    Config.validate()
    Config.setup_langsmith()
    agent = create_fixer()
    logger.info("Fixer agent ready  replica_id=%s", REPLICA_ID)

REPLICA_ID = os.getenv("HOSTNAME", str(uuid.uuid4())[:8])
call_counter = 0

# ── Request / Response Models ─────────────────────────────────────────

class ErrorEvent(BaseModel):
    error_type: str
    message: str

class InvestigationOutput(BaseModel):
    file_path: str
    line_number: int
    method_name: str
    class_name: str
    root_cause: str
    fix_strategy: str
    additional_context: str
    code_snippet: str
    affected_files: List[str] = []

class FixRequest(BaseModel):
    session_id: str
    repo_path: str
    error_event: ErrorEvent
    investigation_output: InvestigationOutput
    rag_context: Optional[str] = None
    reviewer_feedback_context: Optional[str] = None

class FixResponse(BaseModel):
    session_id: str
    replica_id: str
    duration_seconds: float
    result: FixResult

# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "replica_id": REPLICA_ID}

@app.get("/metrics")
def metrics():
    return {"replica_id": REPLICA_ID, "calls_handled": call_counter}

@app.post("/fix", response_model=FixResponse)
def fix(req: FixRequest):
    global call_counter
    call_counter += 1
    start = time.monotonic()

    logger.info(
        "Fix started  session=%s error_type=%s replica=%s",
        req.session_id, req.error_event.error_type, REPLICA_ID
    )

    # Point tools at the cloned repo path
    fixer_tools.set_repo_path(req.repo_path)

    inv = req.investigation_output
    error = req.error_event

    user_message = f"""Fix this error based on the investigation results:

**Error:** {error.error_type}: {error.message}
**File:** {inv.file_path}:{inv.line_number}
**Method:** {inv.method_name} in class {inv.class_name}
**Root Cause:** {inv.root_cause}
**Recommended Strategy:** {inv.fix_strategy}
**Additional Context:** {inv.additional_context}
"""
    if req.rag_context:
        user_message += f"\n{req.rag_context}\n"
    if req.reviewer_feedback_context:
        user_message += f"\n**REVIEWER FEEDBACK (must address):**\n{req.reviewer_feedback_context}\n"
    user_message += "\nRead the file, write the fix, and verify it builds."

    # Invoke the ReAct agent
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})
    except Exception as exc:
        logger.error("Fix failed session=%s error=%s", req.session_id, exc)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    fix = result.get("structured_response")
    if fix is None:
        raise HTTPException(status_code=500, detail="Agent produced no structured output")

    duration = time.monotonic() - start
    logger.info(
        "Fix complete  session=%s duration=%.1fs build_passed=%s attempts=%d",
        req.session_id, duration, fix.build_passed, fix.attempts_made
    )

    return FixResponse(
        session_id=req.session_id,
        replica_id=REPLICA_ID,
        duration_seconds=duration,
        result=fix,
    )


if __name__ == "__main__":
    uvicorn.run("fixer_service:app", host="0.0.0.0", port=8002, workers=1)
