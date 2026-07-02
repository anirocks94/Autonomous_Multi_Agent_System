"""
Investigator Microservice — FastAPI wrapper around the Investigator ReAct agent.

Runs as a separate Kubernetes Deployment so it can be scaled independently
from the Fixer and the Streamlit orchestrator.

POST /investigate
  Body: InvestigateRequest (error_event, repo_path, rag_context)
  Returns: InvestigationResult (Pydantic model)

GET  /health   — Kubernetes liveness / readiness probe
GET  /metrics  — Exposes replica_id and call counter for observability
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
from tools import investigation_tools
from agents.investigator import create_investigator, InvestigationResult

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("investigator-service")

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Investigator Agent Service",
    description="ReAct agent for autonomous C# root-cause analysis",
    version="1.0.0",
)

# ── Agent (initialised once at startup) ──────────────────────────────
agent = None

@app.on_event("startup")
async def startup():
    global agent
    Config.validate()
    Config.setup_langsmith()
    agent = create_investigator()
    logger.info("Investigator agent ready  replica_id=%s", REPLICA_ID)

REPLICA_ID = os.getenv("HOSTNAME", str(uuid.uuid4())[:8])
call_counter = 0

# ── Request / Response Models ─────────────────────────────────────────

class ErrorEvent(BaseModel):
    error_type: str
    message: str
    stack_trace: str
    frequency: int = 1

class InvestigateRequest(BaseModel):
    session_id: str
    repo_path: str
    error_event: ErrorEvent
    rag_context: Optional[str] = None

class InvestigateResponse(BaseModel):
    session_id: str
    replica_id: str
    duration_seconds: float
    result: InvestigationResult

# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "replica_id": REPLICA_ID}

@app.get("/metrics")
def metrics():
    return {"replica_id": REPLICA_ID, "calls_handled": call_counter}

@app.post("/investigate", response_model=InvestigateResponse)
def investigate(req: InvestigateRequest):
    global call_counter
    call_counter += 1
    start = time.monotonic()

    logger.info(
        "Investigation started  session=%s error_type=%s replica=%s",
        req.session_id, req.error_event.error_type, REPLICA_ID
    )

    # Point tools at the cloned repo path
    investigation_tools.set_repo_path(req.repo_path)

    # Build user message
    error = req.error_event
    user_message = f"""Investigate this runtime error:

**Error Type:** {error.error_type}
**Error Message:** {error.message}
**Stack Trace:**
{error.stack_trace}
**Frequency:** {error.frequency} occurrences
"""
    if req.rag_context:
        user_message += f"\n{req.rag_context}\n"
    user_message += "\nFind the root cause, the exact file and line, and recommend a fix strategy."

    # Invoke the ReAct agent
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})
    except Exception as exc:
        logger.error("Investigation failed session=%s error=%s", req.session_id, exc)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    investigation = result.get("structured_response")
    if investigation is None:
        raise HTTPException(status_code=500, detail="Agent produced no structured output")

    duration = time.monotonic() - start
    logger.info(
        "Investigation complete  session=%s duration=%.1fs strategy=%s confidence=%.2f",
        req.session_id, duration, investigation.fix_strategy, investigation.confidence
    )

    return InvestigateResponse(
        session_id=req.session_id,
        replica_id=REPLICA_ID,
        duration_seconds=duration,
        result=investigation,
    )


if __name__ == "__main__":
    uvicorn.run("investigator_service:app", host="0.0.0.0", port=8001, workers=1)
