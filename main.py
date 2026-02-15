"""Main entry point for the autonomous debugging agent."""
import time
import uuid
from config import Config
from agents.monitor import MonitorAgent
from workflow import create_workflow
from langgraph.types import Command


def _display_graph(workflow):
    """Display and save the workflow graph as Mermaid diagram."""
    try:
        mermaid_text = workflow.get_graph().draw_mermaid()
        print("\n📊 Workflow Graph (Mermaid):")
        print("─" * 40)
        print(mermaid_text)
        print("─" * 40)

        # Save Mermaid source
        with open("workflow_graph.mmd", "w") as f:
            f.write(mermaid_text)
        print("   Saved: workflow_graph.mmd")

        # Try to save PNG (requires graphviz/pygraphviz or mermaid CLI)
        try:
            png_bytes = workflow.get_graph().draw_mermaid_png()
            with open("workflow_graph.png", "wb") as f:
                f.write(png_bytes)
            print("   Saved: workflow_graph.png")
        except Exception:
            print("   (PNG export not available — install mermaid CLI for PNG output)")

    except Exception as e:
        print(f"   ⚠️  Could not render graph: {e}")


def _display_fix_for_review(interrupt_value):
    """Display the fix summary for human review."""
    print("\n" + "=" * 60)
    print("🔍 FIX REVIEW — Human Approval Required")
    print("=" * 60)

    if isinstance(interrupt_value, dict):
        print(f"  Error Type:  {interrupt_value.get('error_type', 'N/A')}")
        print(f"  Message:     {interrupt_value.get('error_message', 'N/A')}")
        print(f"  File:        {interrupt_value.get('file', 'N/A')}:{interrupt_value.get('line', 'N/A')}")
        print(f"  Strategy:    {interrupt_value.get('strategy', 'N/A')}")
        print(f"  Confidence:  {interrupt_value.get('confidence', 'N/A')}")
        print(f"  Attempt:     {interrupt_value.get('attempt', 'N/A')}")
        print(f"\n  Fixed Code Preview:")
        print("  " + "─" * 50)
        preview = interrupt_value.get("fixed_code_preview", "N/A")
        for line in preview.split("\n"):
            print(f"  {line}")
        print("  " + "─" * 50)
    else:
        print(f"  Interrupt data: {interrupt_value}")

    print("\n  Options:")
    print("    approve  — Create PR with this fix")
    print("    reject   — Discard fix and stop")
    print("    retry    — Request changes and retry")
    print("=" * 60)


def _get_human_decision() -> dict:
    """Collect human approval decision from terminal input."""
    while True:
        choice = input("\n  Your decision (approve/reject/retry): ").strip().lower()
        if choice in ("approve", "reject", "retry"):
            feedback = ""
            if choice in ("reject", "retry"):
                feedback = input("  Feedback (optional): ").strip()
            status = "approved" if choice == "approve" else (
                "changes_requested" if choice == "retry" else "rejected"
            )
            return {"decision": status, "feedback": feedback}
        print("  Invalid choice. Please enter 'approve', 'reject', or 'retry'.")


def _run_with_approval(workflow, initial_state):
    """Run workflow with interrupt-based human approval."""
    thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    print(f"\n{'=' * 60}")
    print(f"🚨 Processing Error: {initial_state['error_event']['error_type']}")
    print(f"{'=' * 60}")

    # Stream events until the workflow finishes or pauses at interrupt
    final_state = None
    for event in workflow.stream(initial_state, config=thread_config, stream_mode="updates"):
        # Each event is {node_name: state_update}
        for node_name, update in event.items():
            if isinstance(update, dict) and "status" in update:
                final_state = update

    # Check if the workflow is paused at an interrupt
    snapshot = workflow.get_state(thread_config)

    while snapshot.next:
        # Workflow is paused — check for interrupt values
        if snapshot.tasks:
            for task in snapshot.tasks:
                if hasattr(task, 'interrupts') and task.interrupts:
                    interrupt_value = task.interrupts[0].value
                    _display_fix_for_review(interrupt_value)

                    human_decision = _get_human_decision()

                    # Resume the workflow with the human's decision
                    for event in workflow.stream(
                        Command(resume=human_decision),
                        config=thread_config,
                        stream_mode="updates"
                    ):
                        for node_name, update in event.items():
                            if isinstance(update, dict) and "status" in update:
                                final_state = update

        # Check if there are more interrupts
        snapshot = workflow.get_state(thread_config)

    # Get the final state
    final_snapshot = workflow.get_state(thread_config)
    if final_snapshot.values:
        final_state = final_snapshot.values

    return final_state


def _print_summary(final_state):
    """Print workflow summary."""
    print(f"\n{'=' * 60}")
    print("📊 WORKFLOW SUMMARY")
    print(f"{'=' * 60}")
    print(f"Status: {final_state.get('status', 'unknown')}")
    print(f"Session ID: {final_state.get('session_id', 'unknown')}")
    print(f"Attempts: {final_state.get('current_attempt', 'N/A')}")
    print(f"Model: {Config.AZURE_OPENAI_DEPLOYMENT}")

    if final_state.get('status') == 'pr_created':
        print(f"✅ PR Created: {final_state.get('pr_url', 'N/A')}")
    elif final_state.get('status') == 'failed':
        print(f"❌ Failed: {final_state.get('failure_reason', 'Unknown')}")
    elif final_state.get('status') == 'rejected':
        approval = final_state.get('approval')
        feedback = approval.get('reviewer_feedback', '') if approval else ''
        print(f"🚫 Rejected by reviewer: {feedback}")

    parallel_count = len(final_state.get('parallel_fix_attempts', []))
    if parallel_count > 0:
        print(f"Parallel strategies evaluated: {parallel_count}")

    build_errors = final_state.get('build_errors', [])
    if build_errors:
        print(f"Build errors (self-corrected): {len(build_errors)}")

    print(f"\nDecision Trail:")
    for decision in final_state.get('decisions', []):
        print(f"  * {decision['agent']}: {decision['reasoning']}")

    print(f"{'=' * 60}\n")


def main():
    """Run the autonomous debugging agent."""
    print("=" * 60)
    print("🤖 Autonomous C# Debugging Agent — Stage 2")
    print("   (Azure OpenAI | Parallel Strategies | Human Approval)")
    print("=" * 60)

    try:
        Config.validate()
        Config.setup_langsmith()
        print("✅ Configuration validated")
        print(f"   Using model: {Config.AZURE_OPENAI_DEPLOYMENT}")
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        return

    monitor = MonitorAgent()
    workflow = create_workflow()

    # Display workflow graph on startup
    _display_graph(workflow)

    print(f"\n📡 Starting monitor (polling every {Config.POLLING_INTERVAL_SECONDS}s)")
    print("Press Ctrl+C to stop\n")

    try:
        while True:
            initial_state = monitor.detect_errors()

            if initial_state:
                try:
                    final_state = _run_with_approval(workflow, initial_state)
                    if final_state:
                        _print_summary(final_state)
                except Exception as e:
                    print(f"\n❌ Workflow error: {e}")
                    import traceback
                    traceback.print_exc()

            print(f"⏳ Waiting {Config.POLLING_INTERVAL_SECONDS}s until next check...")
            time.sleep(Config.POLLING_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\n\n👋 Shutting down agent...")
        print("Goodbye!")


if __name__ == "__main__":
    main()
