"""Analyzer agent - analyzes errors and code context."""
import os
from pathlib import Path
from state import DebugState, CodeContext, Decision
from utils.repo_manager import RepoManager
from utils.csharp_parser import CSharpParser
from datetime import datetime

class AnalyzerAgent:
    """Analyzes errors and fetches code context."""

    def __init__(self):
        """Initialize analyzer agent."""
        self.repo_manager = RepoManager()
        self.parser = CSharpParser()

    def analyze(self, state: DebugState) -> DebugState:
        """Analyze error and fetch code context."""
        print("\n🔬 Analyzer Agent: Analyzing error...")

        error_event = state["error_event"]

        # Clone repository
        print("   Cloning repository...")
        repo_path = self.repo_manager.clone_repo(state["session_id"])
        state["repo_path"] = repo_path

        # Parse stack trace
        parsed = self.parser.parse_stack_trace(error_event["stack_trace"], repo_path)
        if not parsed:
            state["status"] = "failed"
            state["failure_reason"] = "Could not parse stack trace"
            return state

        file_path, line_number = parsed
        method_name = self.parser.extract_method_name(error_event["stack_trace"])

        # Get code context
        full_file_path = os.path.join(repo_path, file_path)
        code_snippet = self.parser.get_code_context(full_file_path, line_number)

        class_name = Path(file_path).stem

        code_context: CodeContext = {
            "file_path": file_path,
            "line_number": line_number,
            "code_snippet": code_snippet,
            "method_name": method_name or "Unknown",
            "class_name": class_name
        }
        state["code_context"] = code_context

        # Categorize error
        category, strategy, confidence = self._categorize_error(
            error_event["error_type"],
            error_event["message"],
            code_snippet
        )

        state["error_category"] = category
        state["fix_strategy"] = strategy
        state["confidence"] = confidence
        state["status"] = "analyzing"

        decision: Decision = {
            "agent": "analyzer",
            "decision_point": "error_categorization",
            "choice": strategy,
            "reasoning": f"Categorized as {category} with confidence {confidence:.2f}",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        print(f"   ✅ Error categorized: {category}")
        print(f"   Fix strategy: {strategy}")
        print(f"   Confidence: {confidence:.2f}")

        return state

    def _categorize_error(self, error_type: str, message: str, code: str) -> tuple:
        """Categorize error and determine fix strategy."""
        if "KeyNotFoundException" in error_type:
            if "[" in code and "]" in code:
                return ("missing_dictionary_check", "use_trygetvalue", 0.85)
            return ("missing_null_check", "add_guard_clause", 0.75)

        elif "IndexOutOfRangeException" in error_type:
            return ("missing_bounds_check", "check_collection_length", 0.80)

        elif "DivideByZeroException" in error_type:
            return ("division_by_zero", "add_zero_check", 0.90)

        elif "NullReferenceException" in error_type:
            return ("null_reference", "add_null_check", 0.80)

        elif "ParseException" in error_type or "FormatException" in error_type:
            return ("input_parsing_error", "add_input_validation", 0.75)

        elif "ArgumentException" in error_type or "ArgumentNullException" in error_type:
            return ("invalid_argument", "add_argument_validation", 0.80)

        else:
            return ("unknown", "defensive_try_catch", 0.50)
