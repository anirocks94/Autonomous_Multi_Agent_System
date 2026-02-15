"""Analyzer agent - analyzes errors and code context using AI."""
import os
import json
from pathlib import Path
from openai import AzureOpenAI
from state import DebugState, CodeContext, Decision, AnalysisResult
from utils.repo_manager import RepoManager
from utils.csharp_parser import CSharpParser
from config import Config
from datetime import datetime


class AnalyzerAgent:
    """Analyzes errors and fetches code context using Azure OpenAI."""

    def __init__(self):
        """Initialize analyzer agent."""
        self.repo_manager = RepoManager()
        self.parser = CSharpParser()
        self.client = AzureOpenAI(
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT
        )
        self.deployment = Config.AZURE_OPENAI_DEPLOYMENT

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

        # AI-powered analysis (with rule-based fallback)
        analysis = self._analyze_error_with_llm(
            error_event["error_type"],
            error_event["message"],
            code_snippet
        )

        state["analysis_result"] = analysis
        state["error_category"] = analysis["category"]
        state["fix_strategy"] = analysis["strategy"]
        state["confidence"] = analysis["confidence"]
        state["parallel_strategies"] = [analysis["strategy"]] + analysis["alternative_strategies"]
        state["status"] = "analyzing"

        decision: Decision = {
            "agent": "analyzer",
            "decision_point": "error_categorization",
            "choice": analysis["strategy"],
            "reasoning": f"AI analysis: {analysis['reasoning']} (confidence {analysis['confidence']:.2f})",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        print(f"   ✅ Error categorized: {analysis['category']}")
        print(f"   Primary strategy: {analysis['strategy']}")
        print(f"   Alternative strategies: {analysis['alternative_strategies']}")
        print(f"   Confidence: {analysis['confidence']:.2f}")
        print(f"   Reasoning: {analysis['reasoning']}")

        return state

    def _analyze_error_with_llm(self, error_type: str, message: str, code: str) -> AnalysisResult:
        """Analyze error using Azure OpenAI LLM."""
        print("   Calling Azure OpenAI for error analysis...")

        prompt = f"""Analyze this C# error and recommend fix strategies.

**Error Type:** {error_type}
**Error Message:** {message}

**Code Context:**
```csharp
{code}
```

Respond with ONLY a JSON object (no markdown, no code fences):
{{
    "category": "short category name (e.g. missing_null_check, dictionary_key_missing)",
    "strategy": "primary fix strategy name (e.g. use_trygetvalue, add_null_check)",
    "confidence": 0.85,
    "reasoning": "one sentence explaining why this strategy is best",
    "alternative_strategies": ["strategy_2", "strategy_3"]
}}

Rules:
- confidence should be 0.0 to 1.0
- provide exactly 2 alternative strategies
- strategy names should be snake_case action phrases
- be specific to the actual error and code shown
"""

        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a C# debugging expert. Analyze errors and return structured JSON with fix strategies. Return ONLY valid JSON, no markdown formatting."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_completion_tokens=2000
            )

            response_text = response.choices[0].message.content.strip()

            # Strip markdown code fences if present
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                response_text = "\n".join(lines)

            result = json.loads(response_text)

            analysis: AnalysisResult = {
                "category": result.get("category", "unknown"),
                "strategy": result.get("strategy", "defensive_try_catch"),
                "confidence": float(result.get("confidence", 0.5)),
                "reasoning": result.get("reasoning", "LLM analysis"),
                "alternative_strategies": result.get("alternative_strategies", ["defensive_try_catch", "add_logging"])
            }

            print(f"   AI analysis complete: {analysis['category']}")
            return analysis

        except Exception as e:
            print(f"   ⚠️  LLM analysis failed ({e}), falling back to rule-based")
            return self._categorize_error_fallback(error_type, message, code)

    def _categorize_error_fallback(self, error_type: str, message: str, code: str) -> AnalysisResult:
        """Rule-based fallback for error categorization."""
        if "KeyNotFoundException" in error_type:
            if "[" in code and "]" in code:
                return {
                    "category": "missing_dictionary_check",
                    "strategy": "use_trygetvalue",
                    "confidence": 0.85,
                    "reasoning": "Dictionary access without TryGetValue detected",
                    "alternative_strategies": ["add_containskey_check", "add_null_coalescing"]
                }
            return {
                "category": "missing_null_check",
                "strategy": "add_guard_clause",
                "confidence": 0.75,
                "reasoning": "Key access without guard clause",
                "alternative_strategies": ["use_trygetvalue", "add_default_value"]
            }

        elif "IndexOutOfRangeException" in error_type:
            return {
                "category": "missing_bounds_check",
                "strategy": "check_collection_length",
                "confidence": 0.80,
                "reasoning": "Collection access without bounds checking",
                "alternative_strategies": ["use_elementat_or_default", "add_empty_check"]
            }

        elif "DivideByZeroException" in error_type:
            return {
                "category": "division_by_zero",
                "strategy": "add_zero_check",
                "confidence": 0.90,
                "reasoning": "Division without zero-value guard",
                "alternative_strategies": ["use_safe_division_helper", "add_input_validation"]
            }

        elif "NullReferenceException" in error_type:
            return {
                "category": "null_reference",
                "strategy": "add_null_check",
                "confidence": 0.80,
                "reasoning": "Null dereference detected",
                "alternative_strategies": ["use_null_conditional", "add_guard_clause"]
            }

        elif "ParseException" in error_type or "FormatException" in error_type:
            return {
                "category": "input_parsing_error",
                "strategy": "add_input_validation",
                "confidence": 0.75,
                "reasoning": "Input parsing without validation",
                "alternative_strategies": ["use_tryparse", "add_try_catch"]
            }

        elif "ArgumentException" in error_type or "ArgumentNullException" in error_type:
            return {
                "category": "invalid_argument",
                "strategy": "add_argument_validation",
                "confidence": 0.80,
                "reasoning": "Missing argument validation",
                "alternative_strategies": ["add_guard_clause", "use_default_value"]
            }

        else:
            return {
                "category": "unknown",
                "strategy": "defensive_try_catch",
                "confidence": 0.50,
                "reasoning": "Unknown error type - applying defensive strategy",
                "alternative_strategies": ["add_logging", "add_null_check"]
            }
