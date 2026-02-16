"""Validation agent - decides retry vs escalate based on review feedback."""
from typing import Tuple
from state import DebugState, Decision, ParsedReviewFeedback
from config import Config
from datetime import datetime


class ValidationAgent:
    """Analyzes parsed review feedback and decides next action."""

    def __init__(self):
        """Initialize validation agent."""
        pass

    def validate(self, state: DebugState) -> DebugState:
        """Analyze parsed feedback and decide: retry, escalate, or done."""
        print("\n🔍 Validation Agent: Evaluating review feedback...")

        parsed = state.get("parsed_feedback")
        if not parsed:
            state["status"] = "failed"
            state["failure_reason"] = "No parsed feedback to validate"
            return state

        should_escalate, reason = self._should_escalate(state)

        if should_escalate:
            state["status"] = "escalated"
            decision_choice = "escalate"
            print(f"   ⬆️  Escalating: {reason}")
        elif parsed["approval_status"] == "approved":
            state["status"] = "pr_created"
            decision_choice = "done"
            print(f"   ✅ PR approved by reviewer!")
        else:
            # Retry: build feedback context for codegen
            feedback_ctx = self._build_feedback_context(parsed)
            state["reviewer_feedback_context"] = feedback_ctx
            state["status"] = "incorporating_feedback"
            decision_choice = "incorporate_feedback"
            print(f"   🔄 Incorporating feedback: {parsed['overall_summary']}")

        decision: Decision = {
            "agent": "validator",
            "decision_point": "feedback_evaluation",
            "choice": decision_choice,
            "reasoning": reason if should_escalate else parsed["overall_summary"],
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        return state

    def _should_escalate(self, state: DebugState) -> Tuple[bool, str]:
        """Determine if the issue should be escalated to human developers."""
        parsed = state["parsed_feedback"]

        # Trigger 1: Critical sentiment
        if parsed["sentiment"] == "critical":
            return True, "Reviewer feedback is critically negative"

        # Trigger 2: Explicit rejection
        if parsed["approval_status"] == "rejected":
            return True, "Reviewer explicitly rejected the fix"

        # Trigger 3: Too many attempts already
        current = state["current_attempt"]
        max_att = state["max_attempts"]
        if current >= max_att:
            return True, f"Max attempts ({max_att}) exhausted with feedback still pending"

        # Trigger 4: Low confidence after feedback
        if state["confidence"] < Config.CONFIDENCE_THRESHOLD and current > 1:
            return True, (
                f"Confidence {state['confidence']:.2f} below threshold "
                f"after {current} attempts"
            )

        # Trigger 5: Architectural change requests (keyword detection)
        arch_keywords = [
            "refactor", "redesign", "architecture", "rewrite",
            "different approach", "fundamental", "structural", "major change"
        ]
        all_requests = " ".join(parsed.get("change_requests", [])).lower()
        if any(kw in all_requests for kw in arch_keywords):
            return True, "Reviewer requests architectural changes beyond AI fix scope"

        return False, ""

    def _build_feedback_context(self, parsed: ParsedReviewFeedback) -> str:
        """Convert structured feedback into prompt context for codegen."""
        lines = ["**Reviewer Feedback (incorporate these changes):**"]
        for i, req in enumerate(parsed.get("change_requests", []), 1):
            lines.append(f"{i}. {req}")

        if parsed.get("affected_lines"):
            lines.append(f"\nAffected lines: {parsed['affected_lines']}")
        if parsed.get("affected_files"):
            lines.append(f"Affected files: {parsed['affected_files']}")

        return "\n".join(lines)
