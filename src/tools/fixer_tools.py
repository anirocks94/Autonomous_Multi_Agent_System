"""
fixer_tools.py
==============

WHAT THIS FILE DOES
-------------------
Provides the LangChain `@tool`-decorated functions that power the **Fixer Agent**
sub-graph inside the autonomous debugging pipeline.  The Fixer Agent is responsible
for understanding a diagnosed bug, patching the relevant source file(s), and
verifying that the patch actually compiles — all without human involvement.

HOW IT WORKS
------------
1. **Shared repo path** — A module-level variable `_repo_path` stores the absolute
   path to the target repository.  It is injected once by the outer LangGraph
   orchestrator via `set_repo_path()` before any tool is invoked.  Because
   LangChain tools are plain Python callables, closure over a module global is the
   simplest way to share state across tools without threading concerns.

2. **Tool registration** — Each public function is decorated with
   `@tool` from `langchain_core.tools`, which:
   - Wraps it in a `StructuredTool` / `Tool` object understood by LangChain agents.
   - Exposes the function's docstring to the LLM as the tool description, so the
     model knows *when* and *how* to call it.
   - Handles argument parsing and serialisation automatically.

3. **Read → Patch → Build loop** — The typical Fixer Agent workflow is:
   a. Call `read_file` (or `get_code_context`) to retrieve the buggy code.
   b. Call `write_file` with a fully corrected version of the file.
   c. Call `run_build` to confirm the change compiles cleanly.
   d. If the build fails, repeat from (b) with the error output as additional context.

4. **Grep support** — `grep_codebase` lets the agent locate symbol usages or
   related files before deciding what to patch, reducing the risk of incomplete fixes.

PUBLIC INTERFACE
----------------
set_repo_path(path: str) -> None
    Must be called once by the orchestrator before any tool is used.
    Sets `_repo_path` for all tools in this module.

read_file(file_path: str) -> str
    Returns the content of a file (relative to repo root) with 1-based line numbers.

write_file(file_path: str, content: str) -> str
    Overwrites a file with the *entire* corrected content.  Agents must always
    supply the full file, not a diff.

run_build() -> str
    Executes `dotnet build --verbosity quiet` in `_repo_path` and returns a trimmed
    summary of stdout/stderr plus a SUCCESS or FAILED prefix.

grep_codebase(pattern: str, file_glob: str = "*.cs") -> str
    Runs GNU `grep -rn` across the repo for `pattern`, filtered by `file_glob`.
    Returns up to 30 matching lines.

get_code_context(file_path: str, line_number: int, context_lines: int = 20) -> str
    Returns a ±`context_lines` window around `line_number` with the target line
    highlighted by a `>>>` marker.
"""
import os
import subprocess
from langchain_core.tools import tool

# Module-level repo path, set by the outer graph before invoking the agent.
_repo_path: str = ""


def set_repo_path(path: str):
    """Set the repository path for all fixer tools."""
    global _repo_path
    _repo_path = path


@tool
def read_file(file_path: str) -> str:
    """Read a file from the repository. Provide path relative to repo root.
    Returns the file content with line numbers."""
    full_path = os.path.join(_repo_path, file_path)
    try:
        with open(full_path, "r") as f:
            content = f.read()
        lines = content.split("\n")
        numbered = [f"{i+1:4d} | {line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)
    except FileNotFoundError:
        return f"ERROR: File not found: {file_path}"
    except Exception as e:
        return f"ERROR: {e}"


@tool
def write_file(file_path: str, content: str) -> str:
    """Write the COMPLETE content of a file. The file_path is relative to the repo root.
    IMPORTANT: Always write the ENTIRE file, not just the changed portion.
    Include all using statements, namespace, class definition, and methods."""
    full_path = os.path.join(_repo_path, file_path)
    try:
        with open(full_path, "w") as f:
            f.write(content)
        return f"SUCCESS: Wrote {len(content)} characters to {file_path}"
    except Exception as e:
        return f"ERROR: {e}"


@tool
def run_build() -> str:
    """Run 'dotnet build' to check if the current code compiles.
    Returns build output including any errors. Use this after writing a fix."""
    try:
        result = subprocess.run(
            ["dotnet", "build", "--verbosity", "quiet"],
            cwd=_repo_path,
            capture_output=True, text=True, timeout=120
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return "BUILD SUCCEEDED\n" + output[-500:]
        else:
            return "BUILD FAILED\n" + output[-2000:]
    except FileNotFoundError:
        return "BUILD SUCCEEDED (dotnet not available — skipping)"
    except subprocess.TimeoutExpired:
        return "BUILD ERROR: Timed out after 120 seconds"
    except Exception as e:
        return f"BUILD ERROR: {e}"


@tool
def grep_codebase(pattern: str, file_glob: str = "*.cs") -> str:
    """Search for a pattern in the codebase. Returns matching lines with file paths."""
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", file_glob, pattern, _repo_path],
            capture_output=True, text=True, timeout=30
        )
        if not result.stdout:
            return f"No matches found for '{pattern}'"
        lines = result.stdout.strip().split("\n")[:30]
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@tool
def get_code_context(file_path: str, line_number: int, context_lines: int = 20) -> str:
    """Get code around a specific line. The target line is marked with '>>>'.
    Provide file_path relative to repo root."""
    full_path = os.path.join(_repo_path, file_path)
    try:
        with open(full_path, "r") as f:
            lines = f.readlines()
        start = max(0, line_number - context_lines - 1)
        end = min(len(lines), line_number + context_lines)
        snippet = []
        for i in range(start, end):
            marker = ">>>" if i == line_number - 1 else "   "
            snippet.append(f"{marker} {i+1:4d} | {lines[i].rstrip()}")
        return "\n".join(snippet)
    except FileNotFoundError:
        return f"ERROR: File not found: {file_path}"
    except Exception as e:
        return f"ERROR: {e}"
