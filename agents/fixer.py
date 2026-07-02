"""Fixer Agent — autonomous code fix generation and validation using ReAct pattern.

This is a TRUE agent: it receives investigation results, autonomously reads code,
writes a fix, runs the build, and self-corrects if the build fails — all within
its own ReAct loop. No external retry or fan-out needed.
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
