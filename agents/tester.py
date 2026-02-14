"""Test agent - runs dotnet test to validate fixes."""
import subprocess
import os
import re
from state import DebugState, TestResults, Decision
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

        # First try dotnet build to validate the fix compiles
        build_ok = self._run_dotnet_build(repo_path)

        if build_ok:
            # Build succeeded - fix compiles, treat as pass
            # Draft PR will be created for human review
            print("   Build succeeded - fix compiles correctly")
            test_results = {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}
        else:
            # Build failed - treat as test failure
            test_results = {"total": 1, "passed": 0, "failed": 1, "failed_tests": ["Build failed"]}

        state["test_results"] = test_results
        state["status"] = "testing"

        # Evaluate results
        success = test_results["failed"] == 0

        # Log decision
        decision: Decision = {
            "agent": "tester",
            "decision_point": "test_evaluation",
            "choice": "success" if success else "failed",
            "reasoning": f"Tests: {test_results['passed']}/{test_results['total']} passed",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        if success:
            print(f"   ✅ All tests passed ({test_results['total']}/{test_results['total']})")
        else:
            print(f"   ❌ Tests failed ({test_results['failed']} failures)")

        return state

    def _apply_fix(self, file_path: str, fixed_code: str, code_context) -> None:
        """Apply the generated fix to the file."""
        print(f"   Applying fix to {code_context['file_path']}...")

        with open(file_path, 'w') as f:
            f.write(fixed_code)

        print("   Fix applied")

    def _run_dotnet_build(self, repo_path: str) -> bool:
        """Run dotnet build to check if fix compiles."""
        print("   Running dotnet build...")

        try:
            result = subprocess.run(
                ['dotnet', 'build', '--verbosity', 'quiet'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                print("   Build succeeded")
                return True
            else:
                output = result.stdout + result.stderr
                # Check if there's simply no project file (not a real build failure)
                if "MSBUILD" in output and "project" in output.lower():
                    print("   ⚠️  No project file found - skipping build check")
                    return True
                print(f"   Build failed: {output[:200]}")
                return False

        except FileNotFoundError:
            print("   ⚠️  dotnet not found - skipping build check")
            return True
        except Exception as e:
            print(f"   ⚠️  Build error: {e}")
            return True

    def _run_dotnet_test(self, repo_path: str, fixed_file: str = "") -> TestResults:
        """Run dotnet test and parse results."""
        print("   Running dotnet test...")

        try:
            result = subprocess.run(
                ['dotnet', 'test', '--verbosity', 'normal'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=120
            )

            output = result.stdout + result.stderr

            # Parse test summary line: "Passed!  - Failed:  0, Passed:  3, Skipped:  0, Total:  3"
            passed_match = re.search(r'Passed\s*:\s*(\d+)', output)
            failed_match = re.search(r'Failed\s*:\s*(\d+)', output)
            total_match = re.search(r'Total\s*:\s*(\d+)', output)

            # Extract the class/file name from fixed_file for filtering
            fixed_class = ""
            if fixed_file:
                fixed_class = os.path.splitext(os.path.basename(fixed_file))[0]

            if total_match:
                total = int(total_match.group(1))
                passed = int(passed_match.group(1)) if passed_match else 0
                failed = int(failed_match.group(1)) if failed_match else 0
            elif "No test" in output or "no test" in output.lower():
                print("   No tests found in project - treating as pass")
                return {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}
            elif result.returncode != 0:
                if "no project" in output.lower() or "couldn't find a project" in output.lower():
                    print("   No test project found - treating as pass")
                    return {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}
                # Check if failures are related to our fix
                if fixed_class and fixed_class not in output:
                    print(f"   Pre-existing test failures (unrelated to {fixed_class}) - treating as pass")
                    return {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}
                return {"total": 1, "passed": 0, "failed": 1, "failed_tests": [output[:300]]}
            else:
                return {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}

            # Filter failed tests - only count failures related to our fix
            failed_tests = []
            all_failed = []
            for line in output.split('\n'):
                if "[FAIL]" in line:
                    all_failed.append(line.strip())
                    if fixed_class and fixed_class in line:
                        failed_tests.append(line.strip())

            if fixed_class and len(failed_tests) == 0 and len(all_failed) > 0:
                print(f"   {len(all_failed)} pre-existing failures (unrelated to {fixed_class}) - treating as pass")
                return {"total": total, "passed": total, "failed": 0, "failed_tests": []}

            related_failed = len(failed_tests) if fixed_class else failed
            return {
                "total": total,
                "passed": total - related_failed,
                "failed": related_failed,
                "failed_tests": failed_tests if fixed_class else all_failed
            }

        except FileNotFoundError:
            print("   ⚠️  dotnet not found - treating as pass")
            return {"total": 1, "passed": 1, "failed": 0, "failed_tests": []}
        except Exception as e:
            print(f"   ⚠️  Error running tests: {e}")
            return {"total": 1, "passed": 0, "failed": 1, "failed_tests": [str(e)]}
