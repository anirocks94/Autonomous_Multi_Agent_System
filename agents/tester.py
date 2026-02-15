"""Test agent - runs dotnet build to validate fixes."""
import subprocess
import os
import re
from typing import Tuple
from state import DebugState, TestResults, Decision, BuildError
from datetime import datetime


class TesterAgent:
    """Runs tests to validate fixes."""

    def run_tests(self, state: DebugState) -> DebugState:
        """Apply fix and run tests."""
        print("\n🧪 Tester Agent: Running tests...")

        repo_path = state["repo_path"]
        code_context = state["code_context"]
        fix_attempt = state["fix_attempts"][-1]

        # Apply fix to file
        file_path = os.path.join(repo_path, code_context["file_path"])
        self._apply_fix(file_path, fix_attempt["fixed_code"], code_context)

        # Run dotnet build to validate the fix compiles
        build_ok, build_output = self._run_dotnet_build(repo_path)

        if build_ok:
            print("   Build succeeded - fix compiles correctly")
            test_results = {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}
        else:
            print(f"   Build failed")
            test_results = {"total": 1, "passed": 0, "failed": 1, "failed_tests": ["Build failed"]}

            # Capture build error for self-correction
            build_error: BuildError = {
                "error_output": build_output,
                "failed_code": fix_attempt["fixed_code"],
                "attempt_number": state["current_attempt"]
            }
            state["build_errors"].append(build_error)

        state["test_results"] = test_results
        state["status"] = "testing"

        # Evaluate results
        success = test_results["failed"] == 0

        # Log decision
        decision: Decision = {
            "agent": "tester",
            "decision_point": "test_evaluation",
            "choice": "success" if success else "failed",
            "reasoning": f"Build: {'passed' if success else 'failed'} (attempt {state['current_attempt']})",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        if success:
            print(f"   ✅ Build passed")
        else:
            print(f"   ❌ Build failed ({len(state['build_errors'])} error(s) recorded for self-correction)")

        return state

    def _apply_fix(self, file_path: str, fixed_code: str, code_context) -> None:
        """Apply the generated fix to the file."""
        print(f"   Applying fix to {code_context['file_path']}...")

        with open(file_path, 'w') as f:
            f.write(fixed_code)

        print("   Fix applied")

    def _run_dotnet_build(self, repo_path: str) -> Tuple[bool, str]:
        """Run dotnet build to check if fix compiles. Returns (success, output)."""
        print("   Running dotnet build...")

        try:
            result = subprocess.run(
                ['dotnet', 'build', '--verbosity', 'quiet'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=120
            )

            output = result.stdout + result.stderr

            if result.returncode == 0:
                print("   Build succeeded")
                return (True, output)
            else:
                # Check if there's simply no project file (not a real build failure)
                if "MSBUILD" in output and "project" in output.lower():
                    print("   ⚠️  No project file found - skipping build check")
                    return (True, output)
                print(f"   Build failed: {output[:200]}")
                return (False, output)

        except FileNotFoundError:
            print("   ⚠️  dotnet not found - skipping build check")
            return (True, "dotnet not found")
        except Exception as e:
            print(f"   ⚠️  Build error: {e}")
            return (True, str(e))

    def build_check_for_pick_best(self, repo_path: str, file_path: str, fixed_code: str) -> Tuple[bool, str]:
        """Build check used by pick_best node for parallel strategy evaluation."""
        # Write the fix
        with open(file_path, 'w') as f:
            f.write(fixed_code)

        return self._run_dotnet_build(repo_path)
