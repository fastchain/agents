"""File read/write tools — safe file operations with path validation."""

import os

from langchain_core.tools import tool

# Restrict operations to the project directory
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _safe_path(path: str) -> str:
    """Resolve and validate that the path stays within the project root."""
    resolved = os.path.realpath(os.path.join(_PROJECT_ROOT, path))
    if not resolved.startswith(_PROJECT_ROOT):
        raise ValueError(f"Path escapes project root: {path}")
    return resolved


@tool
def read_file(path: str) -> str:
    """Read the contents of a file within the project directory.

    Use this tool to inspect configuration files, source code, skill
    documents, or any text file the agent needs to reference.

    Args:
        path: Relative path from the project root (e.g. "skills/code-review/SKILL.md").
    """
    try:
        full = _safe_path(path)
        with open(full) as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file within the project directory.

    Use this tool when the user asks you to create or update a file, such
    as saving a report, creating a config, or generating code.

    Args:
        path: Relative path from the project root.
        content: The full text content to write.
    """
    try:
        full = _safe_path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to {path}"
    except Exception as e:
        return f"Error writing file: {e}"
