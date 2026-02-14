"""LangGraph workflow orchestration."""
from langgraph.graph import StateGraph, END
from state import DebugState
from agents.analyzer import AnalyzerAgent
from agents.codegen import CodeGenAgent
from agents.tester import TesterAgent
from agents.pr_creator import PRCreatorAgent
from typing import Literal


def create_workflow() -> StateGraph:
    """Create the debugging workflow graph."""
    analyzer = AnalyzerAgent()
    codegen = CodeGenAgent()
    tester = TesterAgent()
    pr_creator = PRCreatorAgent()

    def increment_attempt(state: DebugState) -> DebugState:
        """Increment the attempt counter before retrying."""
        state["current_attempt"] = state["current_attempt"] + 1
        print(f"\n🔄 Tests failed. Retrying (attempt {state['current_attempt']}/{state['max_attempts']})...")
        return state

    workflow = StateGraph(DebugState)

    workflow.add_node("analyze", analyzer.analyze)
    workflow.add_node("generate", codegen.generate_fix)
    workflow.add_node("test", tester.run_tests)
    workflow.add_node("increment_attempt", increment_attempt)
    workflow.add_node("create_pr", pr_creator.create_pr)

    workflow.set_entry_point("analyze")

    workflow.add_edge("analyze", "generate")
    workflow.add_edge("generate", "test")

    workflow.add_conditional_edges(
        "test",
        should_retry,
        {
            "retry": "increment_attempt",
            "create_pr": "create_pr",
            "fail": END
        }
    )

    workflow.add_edge("increment_attempt", "generate")
    workflow.add_edge("create_pr", END)

    return workflow.compile()


def should_retry(state: DebugState) -> Literal["retry", "create_pr", "fail"]:
    """Decide whether to retry, create PR, or fail."""
    test_results = state["test_results"]
    current_attempt = state["current_attempt"]
    max_attempts = state["max_attempts"]

    if test_results["failed"] == 0:
        return "create_pr"

    if current_attempt >= max_attempts:
        print(f"\n❌ Max attempts ({max_attempts}) reached. Giving up.")
        return "fail"

    return "retry"
