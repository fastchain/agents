"""
agents/specialist/tools/code_executor.py — Sandboxed Python code execution

Provides a restricted code execution environment for specialist agents
that need to run generated Python code safely.

Usage:
    from agents.specialist.tools import CodeExecutor

    executor = CodeExecutor(timeout=5)
    result = executor.run("print(2 + 2)")
    print(result.stdout)   # "4\\n"
    print(result.to_output_string())

Security measures:
    - Restricted builtins (no open, exec, eval, compile, __import__)
    - Safe import whitelist (math, json, re, etc.)
    - Execution timeout via SIGALRM
    - Captured stdout/stderr
"""

from __future__ import annotations

import io
import signal
import sys
import contextlib
from dataclasses import dataclass, field
from types import ModuleType


# ── Safe import whitelist ──────────────────────────────────────────────────

SAFE_MODULES = frozenset({
    "math",
    "json",
    "re",
    "string",
    "collections",
    "itertools",
    "functools",
    "operator",
    "textwrap",
    "datetime",
    "decimal",
    "fractions",
    "random",
    "statistics",
    "hashlib",
    "base64",
    "copy",
    "dataclasses",
    "enum",
    "typing",
    "abc",
})

# Builtins to remove from the sandbox
BLOCKED_BUILTINS = frozenset({
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "globals",
    "locals",
    "breakpoint",
    "exit",
    "quit",
    "input",
    "help",
    "memoryview",
})


# ── Data class ─────────────────────────────────────────────────────────────


@dataclass
class ExecutionResult:
    """Result of a sandboxed code execution."""

    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    timed_out: bool = False
    return_value: object = None

    @property
    def success(self) -> bool:
        return self.error is None and not self.timed_out

    def to_output_string(self) -> str:
        """Format result for inclusion in an LLM response."""
        parts = []
        if self.timed_out:
            parts.append("[TIMED OUT]")
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr}")
        if self.error:
            parts.append(f"error:\n{self.error}")
        if not parts:
            parts.append("[no output]")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "timed_out": self.timed_out,
            "success": self.success,
        }


# ── Sandbox helpers ────────────────────────────────────────────────────────


def _make_safe_import(allowed: frozenset[str]):
    """Create a restricted __import__ that only allows whitelisted modules."""

    def safe_import(name, *args, **kwargs):
        top_level = name.split(".")[0]
        if top_level not in allowed:
            raise ImportError(
                f"Import of '{name}' is not allowed. "
                f"Allowed modules: {', '.join(sorted(allowed))}"
            )
        return __builtins__.__import__(name, *args, **kwargs) if isinstance(__builtins__, ModuleType) else __import__(name, *args, **kwargs)

    return safe_import


def _make_sandbox_globals(allowed_modules: frozenset[str]) -> dict:
    """Build a restricted globals dict for code execution."""
    import builtins as _builtins

    safe_builtins = {
        k: v
        for k, v in vars(_builtins).items()
        if k not in BLOCKED_BUILTINS and not k.startswith("_")
    }
    safe_builtins["__import__"] = _make_safe_import(allowed_modules)
    safe_builtins["__name__"] = "__sandbox__"
    safe_builtins["__builtins__"] = safe_builtins

    return {"__builtins__": safe_builtins}


class _Timeout:
    """Context manager for SIGALRM-based timeout (Unix only)."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        self._old_handler = None

    def _handler(self, signum, frame):
        raise TimeoutError(f"Execution timed out after {self.seconds}s")

    def __enter__(self):
        if hasattr(signal, "SIGALRM"):
            self._old_handler = signal.signal(signal.SIGALRM, self._handler)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            if self._old_handler is not None:
                signal.signal(signal.SIGALRM, self._old_handler)
        return False


# ── Executor ───────────────────────────────────────────────────────────────


class CodeExecutor:
    """
    Sandboxed Python code executor with timeout and restricted builtins.

    Integrates with a specialist agent's _process_task() method for tasks
    that require code execution:

        executor = CodeExecutor(timeout=10)

        def _process_task(self, task):
            if task.payload.get("type") == "code_execution":
                result = executor.run(task.payload["code"])
                return {"output": result.to_output_string(), "success": result.success}
    """

    def __init__(
        self,
        timeout: int = 10,
        allowed_modules: frozenset[str] | None = None,
    ):
        """
        Args:
            timeout: Maximum execution time in seconds (uses SIGALRM on Unix).
            allowed_modules: Set of importable module names. Defaults to SAFE_MODULES.
        """
        self.timeout = timeout
        self.allowed_modules = allowed_modules or SAFE_MODULES

    def run(self, code: str) -> ExecutionResult:
        """
        Execute Python code in a sandboxed environment.

        Args:
            code: Python source code to execute.

        Returns:
            ExecutionResult with stdout, stderr, error info, and timeout status.
        """
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        sandbox_globals = _make_sandbox_globals(self.allowed_modules)

        try:
            compiled = compile(code, "<sandbox>", "exec")
        except SyntaxError as e:
            return ExecutionResult(error=f"SyntaxError: {e}")

        try:
            with _Timeout(self.timeout):
                with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                    exec(compiled, sandbox_globals)  # noqa: S102
        except TimeoutError:
            return ExecutionResult(
                stdout=stdout_buf.getvalue(),
                stderr=stderr_buf.getvalue(),
                timed_out=True,
                error="Execution timed out",
            )
        except ImportError as e:
            return ExecutionResult(
                stdout=stdout_buf.getvalue(),
                stderr=stderr_buf.getvalue(),
                error=str(e),
            )
        except Exception as e:
            return ExecutionResult(
                stdout=stdout_buf.getvalue(),
                stderr=stderr_buf.getvalue(),
                error=f"{type(e).__name__}: {e}",
            )

        return ExecutionResult(
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
        )
