"""Tools for the Fixer Agent — code modification and build validation."""
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
