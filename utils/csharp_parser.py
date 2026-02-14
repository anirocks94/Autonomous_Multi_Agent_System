"""C# stack trace and code parsing utilities."""
import re
from typing import Optional, Tuple

class CSharpParser:
    """Parse C# errors and code."""

    @staticmethod
    def parse_stack_trace(stack_trace: str, repo_path: str = "") -> Optional[Tuple[str, int]]:
        """Parse stack trace to extract file path and line number."""
        pattern = r'in (.+\.cs):line (\d+)'
        match = re.search(pattern, stack_trace)

        if match:
            file_path = match.group(1)
            line_number = int(match.group(2))

            # Convert absolute path to relative path within the repo
            if repo_path and file_path.startswith('/'):
                import os
                # Try to find the file relative to repo_path
                for root, dirs, files in os.walk(repo_path):
                    for f in files:
                        full = os.path.join(root, f)
                        if full.endswith(os.path.basename(file_path)):
                            file_path = os.path.relpath(full, repo_path)
                            return (file_path, line_number)
                # Fallback: extract just the path after common project markers
                for marker in ['/Functions/', '/Services/', '/Models/', '/src/']:
                    if marker in file_path:
                        idx = file_path.index(marker)
                        # Go one level up to include the project folder
                        parts = file_path[:idx].rsplit('/', 1)
                        if len(parts) > 1:
                            file_path = file_path[len(parts[0]) + 1:]
                        else:
                            file_path = file_path[idx + 1:]
                        break

            file_path = file_path.replace('/src/', '')
            return (file_path, line_number)

        return None

    @staticmethod
    def extract_method_name(stack_trace: str) -> Optional[str]:
        """Extract method name from stack trace."""
        pattern = r'at .+\.(\w+)\('
        match = re.search(pattern, stack_trace)
        return match.group(1) if match else None

    @staticmethod
    def get_code_context(file_path: str, line_number: int, context_lines: int = 20) -> str:
        """Get code context around a specific line."""
        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()

            start = max(0, line_number - context_lines - 1)
            end = min(len(lines), line_number + context_lines)

            snippet = []
            for i in range(start, end):
                marker = ">>>" if i == line_number - 1 else "   "
                snippet.append(f"{marker} {i+1:4d} | {lines[i].rstrip()}")

            return '\n'.join(snippet)
        except FileNotFoundError:
            return f"File not found: {file_path}"
