"""
investigation_tools.py
=======================

WHAT THIS FILE DOES
-------------------
Provides the LangChain `@tool`-decorated functions that power the **Investigator Agent**
sub-graph inside the autonomous debugging pipeline.  The Investigator Agent is the first
responder: given a raw exception event and a repository path, it autonomously explores
the codebase to locate the root cause before handing its findings to the Fixer Agent.

HOW IT WORKS
------------
1. **Shared repo path** — A module-level variable `_repo_path` stores the absolute
   path to the target repository.  It is injected once by the outer LangGraph
   orchestrator via `set_repo_path()` before any tool is invoked.

2. **Tool registration** — Each public function is decorated with
   `@tool` from `langchain_core.tools`, which wraps it in a `StructuredTool` object
   understood by LangChain agents and exposes the docstring to the LLM as the tool
   description, guiding the model on when and how to invoke each capability.

3. **Investigation workflow** — The typical agent loop is:
   a. `parse_stack_trace` — extract the relevant file path and line number from the
      raw exception traceback.
   b. `get_code_context` — retrieve a windowed snippet around the error line.
   c. `read_file` / `list_files` — inspect related files and directories for broader
      context (e.g., dependency classes, configuration).
   d. `grep_codebase` / `find_references` — locate all usages of a suspicious symbol
      to understand its intended contract.
   e. `git_log` / `git_diff` — check whether the bug was introduced by a recent commit.

4. **Stack trace resolution** — `parse_stack_trace` uses a regex to extract the raw
   absolute file path embedded in a C# stack frame, then walks the repository tree
   with `os.walk` to resolve it to a *relative* path that all other tools can accept.

PUBLIC INTERFACE
----------------
set_repo_path(path: str) -> None
    Must be called once by the orchestrator before any tool is used.

read_file(file_path: str) -> str
    Returns file content (relative path from repo root) with 1-based line numbers.

grep_codebase(pattern: str, file_glob: str = "*.cs") -> str
    Regex search across all matching files.  Returns up to 50 matching lines with
    file paths and line numbers.

list_files(directory: str = "") -> str
    Lists files and sub-directories under a given directory (relative to repo root).
    Useful for mapping the project structure before diving into individual files.

get_code_context(file_path: str, line_number: int, context_lines: int = 20) -> str
    Returns a ±`context_lines` window around `line_number` with the target line
    highlighted by a `>>>` marker.

find_references(symbol: str) -> str
    Greps all `.cs` files for `symbol`, returning file paths and matching lines.
    Useful for tracing how a method or class is called across the codebase.

git_log(file_path: str = "", max_commits: int = 10) -> str
    Returns recent commit history (one-line format) for the repo or a specific file.

git_diff(file_path: str = "", commits_back: int = 1) -> str
    Shows a unified diff of recent changes to the repo or a specific file.
    Output is capped at 3,000 characters to keep token usage reasonable.

parse_stack_trace(stack_trace: str) -> str
    Parses a C# exception stack trace and returns `"relative/path/File.cs:line_number"`.
    Resolves absolute paths to repo-relative paths by walking the directory tree.
"""
import os
import re
import subprocess
from langchain_core.tools import tool

# Module-level repo path, set by the outer graph before invoking the agent.
_repo_path: str = ""


def set_repo_path(path: str):
    """Set the repository path for all investigation tools."""
    global _repo_path
    _repo_path = path


@tool
def read_file(file_path: str) -> str:
    """Read the contents of a file in the repository.
    Provide the path relative to the repository root (e.g. 'Functions/GetUserFunction.cs').
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
def grep_codebase(pattern: str, file_glob: str = "*.cs") -> str:
    """Search for a regex pattern across files in the repository.
    Returns matching lines with file paths and line numbers.
    Use file_glob to filter file types (default: '*.cs')."""
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", file_glob, pattern, _repo_path],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        if not output:
            return f"No matches found for pattern '{pattern}'"
        lines = output.strip().split("\n")[:50]
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@tool
def list_files(directory: str = "") -> str:
    """List files and directories within the repository.
    Provide path relative to repo root. Use empty string for the root directory."""
    target = os.path.join(_repo_path, directory)
    try:
        entries = []
        for item in sorted(os.listdir(target)):
            full = os.path.join(target, item)
            prefix = "[DIR]" if os.path.isdir(full) else "[FILE]"
            entries.append(f"  {prefix} {item}")
        return "\n".join(entries) if entries else "Empty directory"
    except FileNotFoundError:
        return f"ERROR: Directory not found: {directory}"
    except Exception as e:
        return f"ERROR: {e}"


@tool
def get_code_context(file_path: str, line_number: int, context_lines: int = 20) -> str:
    """Get code context around a specific line in a file.
    The error line is marked with '>>>'. Provide file_path relative to repo root."""
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


@tool
def find_references(symbol: str) -> str:
    """Find all references to a symbol (class, method, variable) in .cs files.
    Returns file paths and lines containing the symbol."""
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", "*.cs", symbol, _repo_path],
            capture_output=True, text=True, timeout=30
        )
        if not result.stdout:
            return f"No references found for '{symbol}'"
        lines = result.stdout.strip().split("\n")[:30]
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@tool
def git_log(file_path: str = "", max_commits: int = 10) -> str:
    """Get recent git commit history for the repository or a specific file.
    Provide file_path relative to repo root (optional)."""
    cmd = [
        "git", "-C", _repo_path, "log",
        f"--max-count={max_commits}",
        "--oneline", "--format=%h %s (%an, %ar)"
    ]
    if file_path:
        cmd.append("--")
        cmd.append(file_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout.strip() or "No commits found"
    except Exception as e:
        return f"ERROR: {e}"


@tool
def git_diff(file_path: str = "", commits_back: int = 1) -> str:
    """Show recent changes to the repository or a specific file.
    Use commits_back to control how many commits back to diff against."""
    cmd = ["git", "-C", _repo_path, "diff", f"HEAD~{commits_back}"]
    if file_path:
        cmd.append("--")
        cmd.append(file_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout
        if not output:
            return "No diff found"
        return output[:3000]
    except Exception as e:
        return f"ERROR: {e}"


@tool
def parse_stack_trace(stack_trace: str) -> str:
    """Parse a C# stack trace to extract the file path and line number.
    Returns 'relative/path/File.cs:line_number' or an error message.
    Also searches the repo to resolve the file to a relative path."""
    pattern = r"in (.+\.cs):line (\d+)"
    match = re.search(pattern, stack_trace)
    if not match:
        return "ERROR: Could not parse file path and line number from stack trace"

    raw_file_path = match.group(1)
    line_number = match.group(2)

    # Try to resolve to a relative path within the repo
    basename = os.path.basename(raw_file_path)
    for root, _dirs, files in os.walk(_repo_path):
        if basename in files:
            full = os.path.join(root, basename)
            rel = os.path.relpath(full, _repo_path)
            return f"{rel}:{line_number}"

    return f"{raw_file_path}:{line_number}"
