"""
Supervisor Agent — Centralised Routing for the LangGraph Outer Workflow.

WHAT THIS FILE DOES:
  Implements the "Supervisor" multi-agent pattern: a single deterministic
  component owns ALL routing decisions in the outer graph.  This is an
  intentional contrast to the ReAct agents (Investigator, Fixer) which own
  their own internal decisions.  Having centralised routing makes the graph
  easier to reason about, test, and extend.

THE SUPERVISOR PATTERN (why not put routing logic in edges?):
  LangGraph conditional edges accept a plain Python callable.  Instead of
  inline lambdas scattered across workflow.py, every conditional edge calls
  a method on SupervisorAgent.  Benefits:
    - All routing logic in one file → easy to audit (important for prod AI systems)
    - Rule-based by default → zero LLM latency for happy paths
    - LLM fallback (_llm_route) available for genuinely ambiguous states
    - Every decision is logged to state["supervisor_decisions"] for observability

ROUTING DECISION TREE:
  ┌──────────────────────┬────────────────────────────────────────────┐
  │ route_after_fix      │ build_passed? → approve : fail (END)      │
  │ route_after_approval │ approved → create_pr                       │
  │                      │ changes_requested → retry (back to fix)   │
  │                      │ rejected → END                             │
  │ route_after_poll     │ has_comments → parse_reviews               │
  │                      │ poll_count < max → poll_again              │
  │                      │ timeout → escalate                         │
  │ route_after_review   │ escalated → escalate                       │
  │                      │ pr_created → done (END)                    │
  │                      │ else → incorporate_feedback (back to fix)  │
  └──────────────────────┴────────────────────────────────────────────┘

DECISION LOGGING (_log_decision):
  Every routing call appends a SupervisorDecision TypedDict to
  state["supervisor_decisions"].  The dashboard and _print_summary()
  render these, giving full observability into why the graph took each path.
  The "used_llm" flag distinguishes LLM-assisted decisions from rule-based ones.

LLM FALLBACK (_llm_route):
  Designed for future extension.  Currently not called in any routing method
  (all paths are deterministic).  If an ambiguous state is added, swap one
  return value for self._llm_route(state, point, options, context).
  The LLM is instructed to respond with only the route name — output is
  validated against the allowed options list before use.

INTERVIEW TALKING POINTS:
  - Supervisor pattern vs. per-node edge logic: the former is O(1) to audit,
    the latter scales O(nodes) in complexity.
  - Rule-based routing + LLM fallback is a common production trade-off:
    most cases are deterministic; LLM adds robustness for edge cases.
  - Logging every routing decision into state creates a built-in audit trail
    — compliance-friendly and essential for production AI systems.
"""
from typing import List, Literal
from state import DebugState, SupervisorDecision
from config import Config
from datetime import datetime


class SupervisorAgent:
    """Routes between agents using rule-based logic with optional LLM fallback."""

    def __init__(self):
        """Initialize supervisor with LLM for ambiguous routing."""
        self.llm = Config.get_llm()

    def route_after_triage(self, state: DebugState) -> Literal["clone", "skip"]:
        """Route after the Triage agent completes.

        If FIXABLE, proceed to clone repository. Otherwise skip.
        """
        triage_output = state.get("triage_output")
        if not triage_output:
            choice = "clone"
            reason = "Missing triage output, defaulting to clone"
        elif triage_output["decision"] == "FIXABLE":
            choice = "clone"
            reason = "Triage decision is FIXABLE"
        else:
            choice = "skip"
            reason = f"Triage decision is {triage_output['decision']}: {triage_output['reasoning']}"

        self._log_decision(state, "aftBuer_triage", ["clone", "skip"], choice, reason)
        return choice

    def route_after_fix(self, state: DebugState) -> Literal["approve", "fail"]:
        """Route after the Fixer agent completes.

        Checks whether the fixer produced a compiling fix.
        """
        fix_output = state.get("fix_output")

        if fix_output and fix_output.get("build_passed"):
            choice = "approve"
            reason = f"Fixer agent produced a compiling fix (strategy: {fix_output.get('strategy_used', 'N/A')})"
        else:
            choice = "fail"
            reason = "Fixer agent could not produce a compiling fix"
            state["status"] = "failed"
            state["failure_reason"] = reason

        self._log_decision(state, "after_fix", ["approve", "fail"], choice, reason)
        return choice

    def route_after_approval(self, state: DebugState) -> Literal["create_pr", "retry", "reject"]:
        """Route after human approval gate.

        'retry' routes back to the fix node directly (the Fixer agent
        handles its own internal retries).
        """
        approval = state.get("approval")

        if not approval:
            choice = "reject"
            reason = "No approval data"
        elif approval["status"] == "approved":
            choice = "create_pr"
            reason = "Human approved the fix"
        elif approval["status"] == "changes_requested":
            choice = "retry"
            reason = f"Changes requested: {approval.get('reviewer_feedback', 'no details')}"
            # Store feedback for the fixer agent's next run
            state["reviewer_feedback_context"] = (
                f"**Human reviewer requested changes:**\n{approval.get('reviewer_feedback', '')}"
            )
        else:
            choice = "reject"
            reason = f"Fix rejected: {approval.get('reviewer_feedback', 'no feedback')}"
            state["status"] = "rejected"

        self._log_decision(state, "after_approval", ["create_pr", "retry", "reject"], choice, reason)
        return choice

    def route_after_poll(self, state: DebugState) -> Literal["parse_reviews", "poll_again", "timeout"]:
        """Route after polling for PR review comments."""
        poll_count = state.get("review_poll_count", 0)
        max_polls = state.get("max_review_polls", Config.MAX_REVIEW_POLLS)
        has_comments = bool(state.get("review_comments"))

        if has_comments:
            choice = "parse_reviews"
            reason = f"Found {len(state['review_comments'])} review comment(s)"
        elif poll_count >= max_polls:
            choice = "timeout"
            reason = f"Review polling timed out after {poll_count} polls"
        else:
            choice = "poll_again"
            reason = f"No comments yet (poll {poll_count}/{max_polls})"

        self._log_decision(state, "after_poll", ["parse_reviews", "poll_again", "timeout"], choice, reason)
        return choice

    def route_after_review(self, state: DebugState) -> Literal["incorporate_feedback", "escalate", "done"]:
        """Route after validation agent processes review feedback.

        'incorporate_feedback' routes back to the fix node directly.
        """
        status = state.get("status")

        if status == "escalated":
            choice = "escalate"
            reason = "Validator determined escalation is needed"
        elif status == "pr_created":
            choice = "done"
            reason = "Reviewer approved the PR"
        else:
            choice = "incorporate_feedback"
            reason = "Incorporating reviewer feedback for another attempt"

        self._log_decision(
            state, "after_review",
            ["incorporate_feedback", "escalate", "done"], choice, reason
        )
        return choice

    def _llm_route(self, state: DebugState, decision_point: str,
                   options: List[str], context_summary: str) -> str:
        """Use LLM to make an ambiguous routing decision (fallback)."""
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = f"""You are a workflow supervisor for an autonomous debugging agent.

Current situation: {context_summary}

Available routes: {', '.join(options)}

Which route should we take? Respond with ONLY the route name, nothing else."""

        response = self.llm.invoke([
            SystemMessage(content="You are a routing supervisor. Respond with only the route name."),
            HumanMessage(content=prompt)
        ])
        choice = response.content.strip().lower()

        # Validate choice — fall back to first option if LLM returns invalid
        if choice not in options:
            choice = options[0]

        return choice

    def _log_decision(self, state: DebugState, point: str, options: List[str],
                      choice: str, reason: str, used_llm: bool = False):
        """Log a supervisor routing decision."""
        sd: SupervisorDecision = {
            "decision_point": point,
            "available_routes": options,
            "chosen_route": choice,
            "reasoning": reason,
            "used_llm": used_llm,
            "timestamp": datetime.now()
        }
        state["supervisor_decisions"].append(sd)

        llm_tag = " [LLM]" if used_llm else ""
        print(f"   🧭 Supervisor [{point}]{llm_tag}: {choice} — {reason}")
