"""Investigator Agent — autonomous error investigation using ReAct pattern.

This is a TRUE agent: it receives an error event and autonomously decides
which tools to call (read files, grep, git log, etc.) until it has enough
context to produce a structured root-cause analysis.
"""
from pydantic import BaseModel, Field
from typing import List
from langgraph.prebuilt import create_react_agent
from config import Config
from tools import investigation_tools


class InvestigationResult(BaseModel):
    """Structured output from the Investigator Agent."""
    root_cause: str = Field(description="Root cause analysis of the error")
    error_category: str = Field(
        description="Category like 'missing_null_check', 'dictionary_key_missing', "
                    "'index_out_of_range', 'parse_error', 'divide_by_zero'"
    )
    file_path: str = Field(description="Relative path to the file containing the error")
    line_number: int = Field(description="Line number where the error occurs")
    method_name: str = Field(description="Name of the method containing the error")
    class_name: str = Field(description="Name of the class containing the error")
    code_snippet: str = Field(description="Code context around the error line (20 lines)")
    fix_strategy: str = Field(
        description="Recommended fix strategy, e.g. 'use_trygetvalue', "
                    "'add_null_check', 'add_bounds_check', 'add_try_parse', "
                    "'defensive_try_catch'"
    )
    confidence: float = Field(description="Confidence in the analysis, 0.0 to 1.0")
    additional_context: str = Field(
        description="Any additional findings from the investigation "
                    "(callers, related code, patterns found)"
    )
    affected_files: List[str] = Field(
        description="Other files that reference or are affected by the buggy code"
    )


INVESTIGATOR_PROMPT = """You are an expert C# debugging investigator. Your job is to \
thoroughly investigate a runtime error reported from an Azure Functions application.

You have tools to explore the codebase. Use them systematically:

1. **Parse the stack trace** to find the error file and line number.
2. **Read the file** containing the error to understand the full context.
3. **Get code context** around the error line (use get_code_context).
4. **Search for related code** — find callers, definitions, similar patterns (use grep_codebase and find_references).
5. **List files** if you need to understand the project structure.
6. **Check git history** if relevant (use git_log).

Be thorough but focused:
- Investigate until you understand the root cause.
- Look at how the buggy method is called — the caller may reveal why the error happens.
- Check if there are similar patterns elsewhere that are handled correctly.
- Stop investigating when you have enough information to recommend a fix strategy.

IMPORTANT: You are investigating, not fixing. Report what you find."""


def create_investigator():
    """Create the Investigator ReAct agent."""
    llm = Config.get_llm()

    tools = [
        investigation_tools.read_file,
        investigation_tools.grep_codebase,
        investigation_tools.list_files,
        investigation_tools.get_code_context,
        investigation_tools.find_references,
        investigation_tools.git_log,
        investigation_tools.git_diff,
        investigation_tools.parse_stack_trace,
    ]

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=INVESTIGATOR_PROMPT,
        response_format=InvestigationResult,
        name="investigator",
    )
    return agent
