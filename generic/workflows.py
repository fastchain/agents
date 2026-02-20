"""Temporal workflows and activities for running arbitrary shell commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


@dataclass
class ShellCommandInput:
    command: str
    task_id: str


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@activity.defn
async def validate_command(cmd_input: ShellCommandInput) -> bool:
    """Validate that the command is non-empty.

    Raises on invalid input so the workflow fails fast.
    """
    if not cmd_input.command.strip():
        raise ValueError("Command must not be empty")

    activity.logger.info(
        "Input validated for task %s: command=%r",
        cmd_input.task_id,
        cmd_input.command,
    )
    return True


@activity.defn
async def run_shell_command(cmd_input: ShellCommandInput) -> dict[str, Any]:
    """Execute the shell command as an async subprocess.

    Runs via ``sh -c`` so pipes, redirects and other shell constructs work.
    Heartbeats every 10 seconds to keep Temporal informed that the
    long-running activity is still alive.

    Returns a dict with ``stdout``, ``stderr``, and ``exit_code``.
    """
    cmd = ["sh", "-c", cmd_input.command]

    activity.logger.info("Running: %s", cmd_input.command)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _read_stream(stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    stdout_task = asyncio.create_task(_read_stream(proc.stdout))  # type: ignore[arg-type]
    stderr_task = asyncio.create_task(_read_stream(proc.stderr))  # type: ignore[arg-type]

    # Heartbeat loop — runs until the process finishes
    while not stdout_task.done() or not stderr_task.done():
        activity.heartbeat(f"command running for task {cmd_input.task_id}")
        await asyncio.sleep(10)

    stdout_bytes = await stdout_task
    stderr_bytes = await stderr_task
    exit_code = await proc.wait()

    stdout_str = stdout_bytes.decode("utf-8", errors="replace")
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    if stderr_str.strip():
        activity.logger.warning("stderr: %s", stderr_str)

    activity.logger.info(
        "Command finished for task %s (exit %d, stdout %d bytes, stderr %d bytes)",
        cmd_input.task_id,
        exit_code,
        len(stdout_str),
        len(stderr_str),
    )

    return {
        "stdout": stdout_str,
        "stderr": stderr_str,
        "exit_code": exit_code,
    }


@activity.defn
async def format_output(raw: dict[str, Any]) -> dict[str, Any]:
    """Build a structured result dict with a human-readable summary."""
    stdout: str = raw.get("stdout", "")
    stderr: str = raw.get("stderr", "")
    exit_code: int = raw.get("exit_code", -1)

    summary_parts: list[str] = [f"Command exited with code {exit_code}."]

    if stdout.strip():
        preview = stdout[:2000]
        truncated = len(stdout) > 2000
        summary_parts.append(
            f"\nstdout ({len(stdout)} bytes):\n{preview}"
            + (" [truncated]" if truncated else "")
        )
    else:
        summary_parts.append("\nNo stdout output.")

    if stderr.strip():
        preview = stderr[:500]
        truncated = len(stderr) > 500
        summary_parts.append(
            f"\nstderr ({len(stderr)} bytes):\n{preview}"
            + (" [truncated]" if truncated else "")
        )

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "summary": "\n".join(summary_parts),
    }


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


@workflow.defn
class ShellCommandWorkflow:
    """Orchestrates a shell command run: validate → execute → format output."""

    @workflow.run
    async def run(self, cmd_input: ShellCommandInput) -> dict[str, Any]:
        # Step 1: Validate (fail fast, no retries)
        await workflow.execute_activity(
            validate_command,
            cmd_input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

        # Step 2: Run the command (long-running, heartbeat, retries)
        raw = await workflow.execute_activity(
            run_shell_command,
            cmd_input,
            start_to_close_timeout=timedelta(hours=4),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # Step 3: Format output into structured result
        result = await workflow.execute_activity(
            format_output,
            raw,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        return result
