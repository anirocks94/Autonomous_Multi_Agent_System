"""Main entry point for the autonomous debugging agent."""
import time
from config import Config
from agents.monitor import MonitorAgent
from workflow import create_workflow

def main():
    """Run the autonomous debugging agent."""
    print("=" * 60)
    print("🤖 Autonomous C# Debugging Agent (Azure OpenAI)")
    print("=" * 60)

    try:
        Config.validate()
        print("✅ Configuration validated")
        print(f"   Using model: {Config.AZURE_OPENAI_DEPLOYMENT}")
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        return

    monitor = MonitorAgent()
    workflow = create_workflow()

    print(f"\n📡 Starting monitor (polling every {Config.POLLING_INTERVAL_SECONDS}s)")
    print("Press Ctrl+C to stop\n")

    try:
        while True:
            initial_state = monitor.detect_errors()

            if initial_state:
                print(f"\n{'=' * 60}")
                print(f"🚨 Processing Error: {initial_state['error_event']['error_type']}")
                print(f"{'=' * 60}")

                try:
                    final_state = workflow.invoke(initial_state)

                    print(f"\n{'=' * 60}")
                    print("📊 WORKFLOW SUMMARY")
                    print(f"{'=' * 60}")
                    print(f"Status: {final_state['status']}")
                    print(f"Session ID: {final_state['session_id']}")
                    print(f"Attempts: {final_state['current_attempt']}")
                    print(f"Model: {Config.AZURE_OPENAI_DEPLOYMENT}")

                    if final_state['status'] == 'pr_created':
                        print(f"✅ PR Created: {final_state['pr_url']}")
                    elif final_state['status'] == 'failed':
                        print(f"❌ Failed: {final_state['failure_reason']}")

                    print(f"\nDecision Trail:")
                    for decision in final_state['decisions']:
                        print(f"  • {decision['agent']}: {decision['reasoning']}")

                    print(f"{'=' * 60}\n")

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
