"""LangGraph workflow orchestration with parallel strategies, self-correction, and human approval."""
import os
from langgraph.graph import StateGraph, END
from langgraph.types import Send, interrupt
from langgraph.checkpoint.memory import InMemorySaver
from state import DebugState, Decision
from agents.analyzer import AnalyzerAgent
from agents.codegen import CodeGenAgent
from agents.tester import TesterAgent
from agents.pr_creator import PRCreatorAgent
from typing import Literal
from datetime import datetime


def create_workflow():
    """Create the Stage 2 debugging workflow graph with parallel strategies and human approval."""
    analyzer = AnalyzerAgent()
    codegen = CodeGenAgent()
    tester = TesterAgent()
    pr_creator = PRCreatorAgent()

    # --- Node functions ---

    def fan_out_strategies(state: DebugState) -> list:
        """Fan out to parallel strategy generation using Send API."""
        strategies = state.get("parallel_strategies", [])
        if not strategies:
            strategies = [state.get("fix_strategy", "defensive_try_catch")]

        print(f"\n⚡ Fan-out: Launching {len(strategies)} parallel fix strategies: {strategies}")

        sends = []
        for strategy in strategies:
            sends.append(Send("generate_strategy", {
                **state,
                "fix_strategy": strategy
            }))
        return sends

    def pick_best(state: DebugState) -> DebugState:
        """Pick the best fix from parallel attempts by trying dotnet build on each."""
        print("\n🏆 Pick Best: Evaluating parallel fix attempts...")

        parallel_attempts = state.get("parallel_fix_attempts", [])
        if not parallel_attempts:
            print("   ❌ No parallel fix attempts found")
            state["status"] = "failed"
            state["failure_reason"] = "No fix attempts generated"
            return state

        print(f"   Evaluating {len(parallel_attempts)} fix attempts...")

        code_context = state["code_context"]
        repo_path = state["repo_path"]
        file_path = os.path.join(repo_path, code_context["file_path"])

        # Save original file content to restore between attempts
        try:
            with open(file_path, 'r') as f:
                original_content = f.read()
        except FileNotFoundError:
            original_content = None

        best_index = None
        for i, attempt in enumerate(parallel_attempts):
            print(f"   [{i+1}/{len(parallel_attempts)}] Testing strategy: {attempt['strategy']}...")
            build_ok, build_output = tester.build_check_for_pick_best(
                repo_path, file_path, attempt["fixed_code"]
            )
            if build_ok:
                best_index = i
                print(f"   ✅ Strategy '{attempt['strategy']}' compiles successfully!")
                break
            else:
                print(f"   ❌ Strategy '{attempt['strategy']}' failed to compile")
                # Restore original for next attempt
                if original_content is not None:
                    with open(file_path, 'w') as f:
                        f.write(original_content)

        if best_index is not None:
            state["best_fix_index"] = best_index
            best = parallel_attempts[best_index]
            # Promote the best parallel fix into the main fix_attempts list
            state["fix_attempts"].append(best)
            state["fix_strategy"] = best["strategy"]
            print(f"   🏆 Selected strategy: {best['strategy']}")
        else:
            # None compiled — record build error from last attempt for self-correction
            print("   ❌ No strategy compiled. Will retry if attempts remain.")
            state["best_fix_index"] = None
            if parallel_attempts:
                last = parallel_attempts[-1]
                state["fix_attempts"].append(last)

        decision: Decision = {
            "agent": "pick_best",
            "decision_point": "strategy_selection",
            "choice": parallel_attempts[best_index]["strategy"] if best_index is not None else "none_compiled",
            "reasoning": f"Evaluated {len(parallel_attempts)} strategies, "
                         + (f"selected '{parallel_attempts[best_index]['strategy']}'" if best_index is not None
                            else "none compiled successfully"),
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        return state

    def test_node(state: DebugState) -> DebugState:
        """Run build validation on the selected fix."""
        # If pick_best already found a compiling fix, just confirm
        if state.get("best_fix_index") is not None:
            print("\n🧪 Tester Agent: Build already validated by pick_best")
            state["test_results"] = {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}
            state["status"] = "testing"
            decision: Decision = {
                "agent": "tester",
                "decision_point": "test_evaluation",
                "choice": "success",
                "reasoning": f"Build validated during pick_best (strategy: {state['fix_strategy']})",
                "timestamp": datetime.now()
            }
            state["decisions"].append(decision)
            print(f"   ✅ Build passed")
            return state

        # No fix compiled in pick_best — run tester to record build error
        return tester.run_tests(state)

    def increment_attempt(state: DebugState) -> DebugState:
        """Increment the attempt counter and clear parallel attempts for next round."""
        state["current_attempt"] = state["current_attempt"] + 1
        state["parallel_fix_attempts"] = []
        state["best_fix_index"] = None
        print(f"\n🔄 Retrying (attempt {state['current_attempt']}/{state['max_attempts']})...")
        return state

    def approve_node(state: DebugState):
        """Human-in-the-loop approval gate. Pauses workflow for review."""
        print("\n⏸️  Approval Gate: Waiting for human review...")

        # Build a summary for the reviewer
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

        # interrupt() pauses the graph and returns the summary to the caller
        human_review = interrupt(summary)

        # When resumed, human_review contains the approval decision
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

    # --- Routing functions ---

    def should_retry(state: DebugState) -> Literal["retry", "approve", "fail"]:
        """Decide whether to retry, go to approval, or fail."""
        test_results = state["test_results"]
        current_attempt = state["current_attempt"]
        max_attempts = state["max_attempts"]

        if test_results["failed"] == 0:
            return "approve"

        if current_attempt >= max_attempts:
            print(f"\n❌ Max attempts ({max_attempts}) reached. Giving up.")
            state["status"] = "failed"
            state["failure_reason"] = f"Build failed after {max_attempts} attempts"
            return "fail"

        return "retry"

    def after_approval(state: DebugState) -> Literal["create_pr", "retry", "reject"]:
        """Route based on human approval decision."""
        approval = state.get("approval")
        if not approval:
            return "reject"

        status = approval["status"]
        if status == "approved":
            return "create_pr"
        elif status == "changes_requested":
            # Treat as retry if attempts remain
            if state["current_attempt"] < state["max_attempts"]:
                return "retry"
            return "reject"
        else:
            return "reject"

    # --- Build the graph ---

    workflow = StateGraph(DebugState)

    # Add nodes
    workflow.add_node("analyze", analyzer.analyze)
    workflow.add_node("generate_strategy", codegen.generate_single_strategy)
    workflow.add_node("pick_best", pick_best)
    workflow.add_node("test", test_node)
    workflow.add_node("increment_attempt", increment_attempt)
    workflow.add_node("approve", approve_node)
    workflow.add_node("create_pr", pr_creator.create_pr)

    # Set entry point
    workflow.set_entry_point("analyze")

    # Edges — fan_out_strategies returns Send() objects targeting "generate_strategy"
    workflow.add_conditional_edges(
        "analyze", fan_out_strategies, path_map=["generate_strategy"]
    )
    workflow.add_edge("generate_strategy", "pick_best")
    workflow.add_edge("pick_best", "test")

    workflow.add_conditional_edges(
        "test",
        should_retry,
        {
            "retry": "increment_attempt",
            "approve": "approve",
            "fail": END
        }
    )

    workflow.add_conditional_edges(
        "approve",
        after_approval,
        {
            "create_pr": "create_pr",
            "retry": "increment_attempt",
            "reject": END
        }
    )

    # increment_attempt fans out again
    workflow.add_conditional_edges(
        "increment_attempt", fan_out_strategies, path_map=["generate_strategy"]
    )

    workflow.add_edge("create_pr", END)

    # Compile with checkpointer for interrupt support
    checkpointer = InMemorySaver()
    return workflow.compile(checkpointer=checkpointer)
