"""
Autonomous Debugging Agent — LangGraph Outer Workflow (Stage 5).

WHAT THIS FILE DOES:
  Assembles the top-level LangGraph StateGraph that orchestrates the full
  end-to-end debugging pipeline:  error detection → autonomous investigation →
  autonomous fix → human approval gate → PR creation → PR review loop →
  escalation if needed.  RAG context from past workflows is injected into
  every ReAct agent prompt so the system improves with experience.

THE LANGGRAPH STATE MACHINE:

    START
      │
      ▼
    clone_repo             ← Clones the Azure DevOps repository; notifies Teams
      │
      ▼
    investigate (ReAct)    ← Investigator agent: autonomously calls read_file /
      │                       grep / git_log / parse_stack_trace until it has
      │                       a Pydantic-validated InvestigationResult
      ▼
    fix (ReAct)            ← Fixer agent: reads file → writes fix → runs dotnet
      │                       build → self-corrects on failure (internal loop)
      │
      ├─[build failed]─────────────────────────────────────────► END (failed)
      │
      ▼
    approve (interrupt)    ← Human-in-the-loop gate via LangGraph interrupt();
      │                       workflow suspends here, waiting for a Command(resume=…)
      │
      ├─[approved]──────► create_pr ──► poll_reviews ──► validate_feedback
      ├─[changes_req]──► fix  (retry with reviewer context in prompt)
      └─[rejected]──────────────────────────────────────────────► END
                                                │
      ◄───────────────────────────────── incorporate_feedback
                                                │
                           escalate ◄───────────┘ (critical / max attempts)
                              │
                              ▼
                             END

HOW ROUTING WORKS (Supervisor Pattern):
  Every conditional edge calls a method on SupervisorAgent.  Those methods are
  rule-based (O(1), deterministic) but can optionally fall back to an LLM call
  for ambiguous states.  This is the "Supervisor" pattern:  a single agent owns
  all routing decisions rather than having ad-hoc edge logic scattered across
  the graph.

REACT AGENTS vs. DETERMINISTIC NODES:
  ┌─────────────────┬───────────────────────────────────────────────────┐
  │ ReAct nodes     │ Use create_react_agent; decide tool calls at      │
  │ (investigate,   │ runtime; produce Pydantic structured_response.    │
  │  fix)           │ Adding a new capability = add one @tool function. │
  ├─────────────────┼───────────────────────────────────────────────────┤
  │ Det. nodes      │ Thin wrappers around domain classes (PRCreator,   │
  │ (clone, PR,     │ ReviewParser, etc.).  Predictable, testable,      │
  │  poll, escalate)│ no LLM involved.                                  │
  └─────────────────┴───────────────────────────────────────────────────┘

RAG MEMORY (Stage 5):
  Before each ReAct agent call, DebugMemory.retrieve_context() performs a
  cosine-similarity search in ChromaDB over past workflow outcomes and injects
  the top-k results as additional context in the user message.  Outcomes are
  stored in both ChromaDB (semantic search) and SQLite (structured queries /
  dashboard).

CHECKPOINTING:
  The graph is compiled with InMemorySaver, enabling LangGraph interrupts.
  For production, swap to a durable checkpointer (e.g. SqliteSaver).

PUBLIC INTERFACE:
  create_workflow() → CompiledGraph
  The graph is invoked by main.py (terminal) and dashboard/app.py (Streamlit).
"""
import os
import httpx
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt
from langgraph.checkpoint.sqlite import SqliteSaver
from state import DebugState, Decision, FixAttempt
from nodes.pr_creator import PRCreatorAgent
from nodes.review_parser import ReviewParserAgent
from nodes.validator import ValidationAgent
from nodes.escalator import EscalationAgent
from nodes.triage import TriageAgent
from nodes.supervisor import SupervisorAgent
from utils.repo_manager import RepoManager
from tools import investigation_tools, fixer_tools
from rag.memory import DebugMemory
from datetime import datetime

# ── Microservice URLs ────────────────────────────────────────────────
# In AKS these resolve to ClusterIP services inside the cluster.
# Override via env vars for local dev (e.g. http://localhost:8001).
INVESTIGATOR_URL = os.getenv("INVESTIGATOR_SERVICE_URL", "http://investigator-svc:8001")
FIXER_URL        = os.getenv("FIXER_SERVICE_URL",        "http://fixer-svc:8002")
# Long timeout: investigations take 1-3 min for deep ReAct loops.
HTTP_TIMEOUT = httpx.Timeout(timeout=300.0)



# ── Global Instances (Module-level) ─────────────────────────────
# NOTE: investigator_agent and fixer_agent are NO LONGER created here.
# They run as independent Kubernetes Deployments (investigator-svc, fixer-svc)
# and are called via HTTP from investigate_node / fix_node below.
# This allows each agent to have its own HPA with tuned scaling thresholds:
#   investigator-hpa → memory-led (LLM context accumulation)
#   fixer-hpa        → CPU-led    (dotnet build subprocess spikes)
repo_manager = RepoManager()
pr_creator = PRCreatorAgent()
review_parser = ReviewParserAgent()
validator = ValidationAgent()
escalator = EscalationAgent()
triage_agent = TriageAgent()
supervisor = SupervisorAgent()
memory = DebugMemory.get_instance()


def create_workflow():
    """Create the Stage 5 agentic debugging workflow with RAG memory."""
    # ── Graph Topology ──────────────────────────────────────────────
    workflow = StateGraph(DebugState)

    # Nodes
    workflow.add_node("triage", triage_agent.triage)
    workflow.add_node("clone_repo", clone_repo_node)
    workflow.add_node("investigate", investigate_node)
    workflow.add_node("fix", fix_node)
    workflow.add_node("approve", approve_node)
    workflow.add_node("create_pr", pr_creator.create_pr)
    workflow.add_node("poll_reviews", review_parser.poll_and_parse)
    workflow.add_node("validate_feedback", validator.validate)
    workflow.add_node("escalate", escalator.escalate)

    # Entry point
    workflow.set_entry_point("triage")

    # ── Edges ───────────────────────────────────────────────────────

    # triage → supervisor routes
    workflow.add_conditional_edges(
        "triage",
        supervisor.route_after_triage,
        {
            "clone": "clone_repo",
            "skip": END,
        }
    )

    # clone → investigate → fix
    workflow.add_edge("clone_repo", "investigate")
    workflow.add_edge("investigate", "fix")

    # fix → supervisor routes
    workflow.add_conditional_edges(
        "fix",
        supervisor.route_after_fix,
        {
            "approve": "approve",
            "fail": END,
        }
    )

    # approve → supervisor routes
    workflow.add_conditional_edges(
        "approve",
        supervisor.route_after_approval,
        {
            "create_pr": "create_pr",
            "retry": "fix",
            "reject": END,
        }
    )

    # create_pr → poll_reviews
    workflow.add_edge("create_pr", "poll_reviews")

    # poll_reviews → supervisor routes
    workflow.add_conditional_edges(
        "poll_reviews",
        supervisor.route_after_poll,
        {
            "parse_reviews": "validate_feedback",
            "poll_again": "poll_reviews",
            "timeout": "escalate",
        }
    )

    # validate_feedback → supervisor routes
    workflow.add_conditional_edges(
        "validate_feedback",
        supervisor.route_after_review,
        {
            "incorporate_feedback": "fix",
            "escalate": "escalate",
            "done": END,
        }
    )

    # escalate → END
    workflow.add_edge("escalate", END)

    # Compile with durable checkpointer — SqliteSaver persists interrupt state
    # to the shared Azure Disk PVC so any AKS pod can resume any session.
    # (InMemorySaver would lose checkpoint state if the resume HTTP request
    #  hits a different pod than the one that raised the interrupt.)
    checkpointer = SqliteSaver.from_conn_string(str(Config.SQLITE_DB_PATH))
    return workflow.compile(checkpointer=checkpointer)


# ── Node Implementations ────────────────────────────────────────

# ── Node: Clone Repository ──────────────────────────────────────

def clone_repo_node(state: DebugState) -> DebugState:
    """Clone the Azure DevOps repository."""
    print("\n📦 Cloning repository...")
    repo_path = repo_manager.clone_repo(state["session_id"])
    state["repo_path"] = repo_path

    from tools.teams_notifier import TeamsNotifier
    TeamsNotifier.notify_exception_received(state["session_id"], state["error_event"])

    decision: Decision = {
        "agent": "clone",
        "decision_point": "repo_cloned",
        "choice": "success",
        "reasoning": f"Cloned repo to {repo_path}",
        "timestamp": datetime.now()
    }
    state["decisions"].append(decision)
    memory.record_event(state["session_id"], "clone_repo", {"repo_path": repo_path})
    print(f"   ✅ Repository cloned to {repo_path}")
    return state

# ── Node: Investigate (calls investigator-svc microservice) ──────

def investigate_node(state: DebugState) -> DebugState:
    """Call the Investigator microservice (its own K8s Deployment + HPA)."""
    print("\n🔬 Investigator Service: Sending investigation request...")
    error = state["error_event"]

    # Retrieve RAG context from past workflows
    rag_context = memory.retrieve_context(error["error_type"], error["message"])
    state["rag_context"] = rag_context or None
    if rag_context:
        print("   📚 RAG: Found similar past errors in memory")

    payload = {
        "session_id": state["session_id"],
        "repo_path": str(state["repo_path"]),
        "error_event": {
            "error_type": error["error_type"],
            "message": error["message"],
            "stack_trace": error["stack_trace"],
            "frequency": error.get("frequency", 1),
        },
        "rag_context": rag_context,
    }

    try:
        resp = httpx.post(
            f"{INVESTIGATOR_URL}/investigate",
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        state["status"] = "failed"
        state["failure_reason"] = f"Investigator service error: {exc}"
        print(f"   ❌ Investigator service call failed: {exc}")
        return state

    data = resp.json()
    replica = data.get("replica_id", "unknown")
    print(f"   ✅ Response from investigator replica {replica} in {data['duration_seconds']:.1f}s")

    investigation = data["result"]

    state["investigation_output"] = {
        "root_cause": investigation["root_cause"],
        "error_category": investigation["error_category"],
        "file_path": investigation["file_path"],
        "line_number": investigation["line_number"],
        "method_name": investigation["method_name"],
        "class_name": investigation["class_name"],
        "code_snippet": investigation["code_snippet"],
        "fix_strategy": investigation["fix_strategy"],
        "confidence": investigation["confidence"],
        "additional_context": investigation["additional_context"],
        "affected_files": investigation["affected_files"],
    }
    state["code_context"] = {
        "file_path": investigation["file_path"],
        "line_number": investigation["line_number"],
        "code_snippet": investigation["code_snippet"],
        "method_name": investigation["method_name"],
        "class_name": investigation["class_name"],
    }
    state["error_category"] = investigation["error_category"]
    state["fix_strategy"] = investigation["fix_strategy"]
    state["confidence"] = investigation["confidence"]
    state["status"] = "analyzing"

    decision: Decision = {
        "agent": "investigator",
        "decision_point": "investigation_complete",
        "choice": investigation["fix_strategy"],
        "reasoning": investigation["root_cause"],
        "timestamp": datetime.now()
    }
    state["decisions"].append(decision)
    memory.record_event(state["session_id"], "investigate", {
        "root_cause": investigation["root_cause"],
        "file_path": investigation["file_path"],
        "strategy": investigation["fix_strategy"],
        "confidence": investigation["confidence"],
    })

    print(f"      Root cause: {investigation['root_cause']}")
    print(f"      File: {investigation['file_path']}:{investigation['line_number']}")
    print(f"      Strategy: {investigation['fix_strategy']}")
    print(f"      Confidence: {investigation['confidence']:.2f}")
    return state

# ── Node: Fix (calls fixer-svc microservice) ─────────────────────

def fix_node(state: DebugState) -> DebugState:
    """Call the Fixer microservice (its own K8s Deployment + HPA)."""
    print("\n🛠️  Fixer Service: Sending fix request...")

    inv = state["investigation_output"]
    error = state["error_event"]
    rag_ctx = state.get("rag_context") or None
    reviewer_ctx = state.get("reviewer_feedback_context") or None

    payload = {
        "session_id": state["session_id"],
        "repo_path": str(state["repo_path"]),
        "error_event": {
            "error_type": error["error_type"],
            "message": error["message"],
        },
        "investigation_output": {
            "file_path": inv["file_path"],
            "line_number": inv["line_number"],
            "method_name": inv["method_name"],
            "class_name": inv["class_name"],
            "root_cause": inv["root_cause"],
            "fix_strategy": inv["fix_strategy"],
            "additional_context": inv["additional_context"],
            "code_snippet": inv["code_snippet"],
            "affected_files": inv.get("affected_files", []),
        },
        "rag_context": rag_ctx,
        "reviewer_feedback_context": reviewer_ctx,
    }

    try:
        resp = httpx.post(
            f"{FIXER_URL}/fix",
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        state["status"] = "failed"
        state["failure_reason"] = f"Fixer service error: {exc}"
        print(f"   ❌ Fixer service call failed: {exc}")
        return state

    data = resp.json()
    replica = data.get("replica_id", "unknown")
    print(f"   ✅ Response from fixer replica {replica} in {data['duration_seconds']:.1f}s")

    fix = data["result"]

    state["fix_output"] = {
        "fixed_file_path": fix["fixed_file_path"],
        "strategy_used": fix["strategy_used"],
        "fix_description": fix["fix_description"],
        "build_passed": fix["build_passed"],
        "attempts_made": fix["attempts_made"],
        "final_code": fix["final_code"],
    }

    fix_attempt: FixAttempt = {
        "attempt_number": state["current_attempt"],
        "strategy": fix["strategy_used"],
        "fixed_code": fix["final_code"],
        "reasoning": fix["fix_description"],
    }
    state["fix_attempts"].append(fix_attempt)
    state["fix_strategy"] = fix["strategy_used"]

    if fix["build_passed"]:
        state["test_results"] = {
            "total": 1, "passed": 1, "failed": 0, "failed_tests": []
        }
        state["status"] = "generating"
        print("   ✅ Fix generated and build passed")
    else:
        state["test_results"] = {
            "total": 1, "passed": 0, "failed": 1,
            "failed_tests": ["Build failed after agent attempts"]
        }
        state["status"] = "failed"
        state["failure_reason"] = (
            f"Build failed after {fix['attempts_made']} fixer service attempt(s)"
        )
        print(f"   ❌ Build failed after {fix['attempts_made']} attempt(s)")

    decision: Decision = {
        "agent": "fixer",
        "decision_point": "fix_generated",
        "choice": fix["strategy_used"],
        "reasoning": (
            f"{'Build passed' if fix['build_passed'] else 'Build failed'} "
            f"after {fix['attempts_made']} attempt(s): {fix['fix_description']}"
        ),
        "timestamp": datetime.now()
    }
    state["decisions"].append(decision)
    memory.record_event(state["session_id"], "fix", {
        "strategy": fix["strategy_used"],
        "build_passed": fix["build_passed"],
        "attempts": fix["attempts_made"],
    })

    print(f"      Strategy: {fix['strategy_used']}")
    print(f"      Description: {fix['fix_description']}")
    return state

# ── Node: Approval Gate (interrupt) ─────────────────────────────

def approve_node(state: DebugState):
    """Human-in-the-loop approval gate. Pauses workflow for review."""
    print("\n⏸️  Approval Gate: Waiting for human review...")

    fix = state["fix_attempts"][-1] if state["fix_attempts"] else None
    summary = {
        "error_type": state["error_event"]["error_type"],
        "error_message": state["error_event"]["message"],
        "file": state["code_context"]["file_path"],
        "line": state["code_context"]["line_number"],
        "strategy": state["fix_strategy"],
        "confidence": state["confidence"],
        "attempt": state["current_attempt"],
        "fixed_code_preview": fix["fixed_code"][:500] if fix else "N/A"
    }

    human_review = interrupt(summary)

    approval_status = human_review.get("decision", "rejected")
    feedback = human_review.get("feedback", "")

    state["approval"] = {
        "status": approval_status,
        "reviewer_feedback": feedback,
        "reviewed_at": datetime.now()
    }

    decision: Decision = {
        "agent": "human_reviewer",
        "decision_point": "approval",
        "choice": approval_status,
        "reasoning": feedback or f"Human {approval_status} the fix",
        "timestamp": datetime.now()
    }
    state["decisions"].append(decision)

    if approval_status == "approved":
        state["status"] = "pr_created"
        print(f"   ✅ Fix approved by reviewer")
    elif approval_status == "changes_requested":
        print(f"   🔄 Changes requested: {feedback}")
    else:
        state["status"] = "rejected"
        print(f"   ❌ Fix rejected: {feedback}")

    return state

