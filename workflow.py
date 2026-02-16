"""LangGraph workflow — Stage 4 agentic architecture.

The Investigator and Fixer are true ReAct agents (create_react_agent sub-graphs)
that autonomously decide which tools to call. The outer graph orchestrates them
alongside deterministic nodes (clone, approve, PR, review loop).
"""
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt
from langgraph.checkpoint.memory import InMemorySaver
from state import DebugState, Decision, FixAttempt
from agents.investigator import create_investigator
from agents.fixer import create_fixer
from agents.pr_creator import PRCreatorAgent
from agents.review_parser import ReviewParserAgent
from agents.validator import ValidationAgent
from agents.escalator import EscalationAgent
from agents.supervisor import SupervisorAgent
from utils.repo_manager import RepoManager
from tools import investigation_tools, fixer_tools
from datetime import datetime


def create_workflow():
    """Create the Stage 4 agentic debugging workflow."""
    repo_manager = RepoManager()
    investigator_agent = create_investigator()
    fixer_agent = create_fixer()
    pr_creator = PRCreatorAgent()
    review_parser = ReviewParserAgent()
    validator = ValidationAgent()
    escalator = EscalationAgent()
    supervisor = SupervisorAgent()

    # ── Node: Clone Repository ──────────────────────────────────────

    def clone_repo_node(state: DebugState) -> DebugState:
        """Clone the Azure DevOps repository."""
        print("\n📦 Cloning repository...")
        repo_path = repo_manager.clone_repo(state["session_id"])
        state["repo_path"] = repo_path

        decision: Decision = {
            "agent": "clone",
            "decision_point": "repo_cloned",
            "choice": "success",
            "reasoning": f"Cloned repo to {repo_path}",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)
        print(f"   ✅ Repository cloned to {repo_path}")
        return state

    # ── Node: Investigate (ReAct Agent) ─────────────────────────────

    def investigate_node(state: DebugState) -> DebugState:
        """Invoke the Investigator ReAct agent to autonomously explore the error."""
        print("\n🔬 Investigator Agent: Starting autonomous investigation...")
        repo_path = state["repo_path"]

        # Set repo_path for investigation tools
        investigation_tools.set_repo_path(repo_path)

        # Build the user message with error details
        error = state["error_event"]
        user_message = f"""Investigate this runtime error:

**Error Type:** {error['error_type']}
**Error Message:** {error['message']}
**Stack Trace:**
{error['stack_trace']}
**Frequency:** {error['frequency']} occurrences

Find the root cause, the exact file and line, and recommend a fix strategy."""

        # Invoke the investigator agent
        result = investigator_agent.invoke({
            "messages": [{"role": "user", "content": user_message}]
        })

        # Extract structured response
        investigation = result.get("structured_response")
        if investigation is None:
            state["status"] = "failed"
            state["failure_reason"] = "Investigator agent did not produce structured output"
            print("   ❌ Investigation failed — no structured output")
            return state

        # Store Stage 4 investigation output
        state["investigation_output"] = {
            "root_cause": investigation.root_cause,
            "error_category": investigation.error_category,
            "file_path": investigation.file_path,
            "line_number": investigation.line_number,
            "method_name": investigation.method_name,
            "class_name": investigation.class_name,
            "code_snippet": investigation.code_snippet,
            "fix_strategy": investigation.fix_strategy,
            "confidence": investigation.confidence,
            "additional_context": investigation.additional_context,
            "affected_files": investigation.affected_files,
        }

        # Populate legacy fields for downstream compatibility (PR creator, etc.)
        state["code_context"] = {
            "file_path": investigation.file_path,
            "line_number": investigation.line_number,
            "code_snippet": investigation.code_snippet,
            "method_name": investigation.method_name,
            "class_name": investigation.class_name,
        }
        state["error_category"] = investigation.error_category
        state["fix_strategy"] = investigation.fix_strategy
        state["confidence"] = investigation.confidence
        state["status"] = "analyzing"

        decision: Decision = {
            "agent": "investigator",
            "decision_point": "investigation_complete",
            "choice": investigation.fix_strategy,
            "reasoning": investigation.root_cause,
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        print(f"   ✅ Investigation complete")
        print(f"      Root cause: {investigation.root_cause}")
        print(f"      File: {investigation.file_path}:{investigation.line_number}")
        print(f"      Strategy: {investigation.fix_strategy}")
        print(f"      Confidence: {investigation.confidence:.2f}")
        return state

    # ── Node: Fix (ReAct Agent) ─────────────────────────────────────

    def fix_node(state: DebugState) -> DebugState:
        """Invoke the Fixer ReAct agent to autonomously fix and validate the error."""
        print("\n🛠️  Fixer Agent: Starting autonomous fix generation...")
        repo_path = state["repo_path"]

        # Set repo_path for fixer tools
        fixer_tools.set_repo_path(repo_path)

        # Build user message with investigation context
        inv = state["investigation_output"]
        error = state["error_event"]
        reviewer_ctx = state.get("reviewer_feedback_context") or ""

        user_message = f"""Fix this error based on the investigation results:

**Error:** {error['error_type']}: {error['message']}
**File:** {inv['file_path']}:{inv['line_number']}
**Method:** {inv['method_name']} in class {inv['class_name']}
**Root Cause:** {inv['root_cause']}
**Recommended Strategy:** {inv['fix_strategy']}
**Additional Context:** {inv['additional_context']}
"""
        if reviewer_ctx:
            user_message += f"\n**REVIEWER FEEDBACK (must address):**\n{reviewer_ctx}\n"

        user_message += "\nRead the file, write the fix, and verify it builds."

        # Invoke the fixer agent
        result = fixer_agent.invoke({
            "messages": [{"role": "user", "content": user_message}]
        })

        # Extract structured response
        fix = result.get("structured_response")
        if fix is None:
            state["status"] = "failed"
            state["failure_reason"] = "Fixer agent did not produce structured output"
            print("   ❌ Fix failed — no structured output")
            return state

        # Store Stage 4 fix output
        state["fix_output"] = {
            "fixed_file_path": fix.fixed_file_path,
            "strategy_used": fix.strategy_used,
            "fix_description": fix.fix_description,
            "build_passed": fix.build_passed,
            "attempts_made": fix.attempts_made,
            "final_code": fix.final_code,
        }

        # Populate legacy fields for downstream compatibility
        fix_attempt: FixAttempt = {
            "attempt_number": state["current_attempt"],
            "strategy": fix.strategy_used,
            "fixed_code": fix.final_code,
            "reasoning": fix.fix_description,
        }
        state["fix_attempts"].append(fix_attempt)
        state["fix_strategy"] = fix.strategy_used

        if fix.build_passed:
            state["test_results"] = {
                "total": 1, "passed": 1, "failed": 0, "failed_tests": []
            }
            state["status"] = "generating"
            print(f"   ✅ Fix generated and build passed")
        else:
            state["test_results"] = {
                "total": 1, "passed": 0, "failed": 1,
                "failed_tests": ["Build failed after agent attempts"]
            }
            state["status"] = "failed"
            state["failure_reason"] = (
                f"Build failed after {fix.attempts_made} fixer agent attempt(s)"
            )
            print(f"   ❌ Build failed after {fix.attempts_made} attempt(s)")

        decision: Decision = {
            "agent": "fixer",
            "decision_point": "fix_generated",
            "choice": fix.strategy_used,
            "reasoning": (
                f"{'Build passed' if fix.build_passed else 'Build failed'} "
                f"after {fix.attempts_made} attempt(s): {fix.fix_description}"
            ),
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        print(f"      Strategy: {fix.strategy_used}")
        print(f"      Description: {fix.fix_description}")
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

    # ── Build the graph ─────────────────────────────────────────────

    workflow = StateGraph(DebugState)

    # Nodes
    workflow.add_node("clone_repo", clone_repo_node)
    workflow.add_node("investigate", investigate_node)
    workflow.add_node("fix", fix_node)
    workflow.add_node("approve", approve_node)
    workflow.add_node("create_pr", pr_creator.create_pr)
    workflow.add_node("poll_reviews", review_parser.poll_and_parse)
    workflow.add_node("validate_feedback", validator.validate)
    workflow.add_node("escalate", escalator.escalate)

    # Entry point
    workflow.set_entry_point("clone_repo")

    # ── Edges ───────────────────────────────────────────────────────

    # Linear: clone → investigate → fix
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

    # Compile with checkpointer for interrupt support
    checkpointer = InMemorySaver()
    return workflow.compile(checkpointer=checkpointer)
