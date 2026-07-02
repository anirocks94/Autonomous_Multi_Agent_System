"""
Fixer Agent — Autonomous Code Fix Generation & Validation via ReAct Pattern.

WHAT THIS FILE DOES:
  Defines the Fixer, the second TRUE ReAct agent in the system.  It receives
  the structured InvestigationResult from the Investigator and autonomously:
    1. Reads the buggy file to understand full context.
    2. Writes a complete, corrected version of the file.
    3. Runs dotnet build to verify compilation.
    4. If the build fails, reads the error output, adjusts the fix, and tries
       again — all within a single agent.invoke() call.

SELF-CORRECTION LOOP (internal to the ReAct agent):
    LLM → write_file → run_build → [BUILD FAILED] → LLM reads errors
       → write_file (corrected) → run_build → [BUILD SUCCEEDED] → stop

  This loop is NOT orchestrated by the outer LangGraph graph; it is
  emergent behaviour from the ReAct pattern.  The LLM stops itself when
  build_passed=True or after 3 attempts (enforced by the system prompt).

AVAILABLE TOOLS (from fixer_tools.py):
  read_file         — Read a repository file with line numbers
  write_file        — Overwrite a file with COMPLETE content
  run_build         — Execute `dotnet build` and capture stdout/stderr
  grep_codebase     — Regex search across .cs files (for context)
  get_code_context  — Get ±20 lines around a target line (pre-read context)

STRUCTURED OUTPUT (FixResult — Pydantic BaseModel):
  fixed_file_path — Which file was modified
  strategy_used   — Which fix pattern was applied
  fix_description — Human-readable explanation for PR description
  build_passed    — Boolean: did dotnet build succeed?
  attempts_made   — How many write-build cycles the agent ran
  final_code      — The final file content (stored in state for PR diff)

CRITICAL PROMPT RULES (FIXER_PROMPT):
  - Always write the COMPLETE file (never partial patches) — prevents
    dotnet build errors from missing class members.
  - Keep changes MINIMAL — reduces reviewer rejection risk.
  - ALWAYS run build after writing — enforces the self-correction loop.
  - Report build_passed=false honestly after 3 failures — enables the
    outer graph to route to escalation instead of spinning forever.

INTERVIEW TALKING POINTS:
  - Self-correction (write → build → retry) is purely a prompt + tool
    design — no extra graph nodes needed.  This is the key advantage of
    ReAct over fixed-step graphs for tasks with uncertain outcomes.
  - write_file takes the COMPLETE file because `dotnet build` validates
    the entire compilation unit, not just the changed method.
  - The outer graph's Supervisor routes to escalation when build_passed=false,
    so the Fixer never needs to know about the bigger orchestration.
"""
from pydantic import BaseModel, Field
from langgraph.prebuilt import create_react_agent
from config import Config
from tools import fixer_tools


class FixResult(BaseModel):
    """Structured output from the Fixer Agent."""
    fixed_file_path: str = Field(description="Relative path to the file that was fixed")
    strategy_used: str = Field(description="The fix strategy that was applied")
    fix_description: str = Field(
        description="Description of what was changed and why"
    )
    build_passed: bool = Field(
        description="Whether the fix compiles successfully after dotnet build"
    )
    attempts_made: int = Field(
        description="Number of write-build cycles attempted within this agent run"
    )
    final_code: str = Field(
        description="The final fixed file content that was written"
    )


FIXER_PROMPT = """You are an expert C# developer specializing in fixing bugs in Azure Functions applications.

Your workflow — follow these steps IN ORDER:

1. **Read the file** that needs to be fixed using read_file to understand the full context.
2. **Understand the error** from the investigation results provided in the user message.
3. **Write a fix** by calling write_file with the COMPLETE file content.
4. **Run the build** using run_build to verify the fix compiles.
5. **If the build fails**, read the build error output carefully, adjust your fix, and try again (go back to step 3).
6. **Repeat** until the build succeeds or you have tried 3 times.

CRITICAL RULES:
- Always write the COMPLETE file content — all using statements, namespace, class definition, constructors, and ALL methods must be present.
- NEVER write partial files or just the changed method.
- Keep changes MINIMAL — fix only what is necessary to resolve the reported error.
- Do NOT add unnecessary using statements.
- Do NOT rename or restructure existing code.
- Do NOT change method signatures or class structure.
- ALWAYS run the build after writing a fix to verify it compiles.
- If using grep_codebase or get_code_context, use them to understand related code before writing the fix.

If after 3 attempts the build still fails, report build_passed=false honestly."""


def create_fixer():
    """Create the Fixer ReAct agent."""
    llm = Config.get_llm()

    tools = [
        fixer_tools.read_file,
        fixer_tools.write_file,
        fixer_tools.run_build,
        fixer_tools.grep_codebase,
        fixer_tools.get_code_context,
    ]

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=FIXER_PROMPT,
        response_format=FixResult,
        name="fixer",
    )
    return agent
