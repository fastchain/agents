"""MCP server exposing shell command execution tools backed by Temporal workflows."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from temporalio.client import Client

from workflows import ShellCommandInput, ShellCommandWorkflow

# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

INSTRUCTIONS = """\
You are a shell command execution assistant. You help users run arbitrary shell \
commands on the worker host, orchestrated through Temporal workflows for \
reliability and observability.

## Workflow: start → poll → get results

1. Use `start_command()` to kick off a command. It returns a `task_id` immediately.
2. Use `check_task_status(task_id)` to poll whether it has finished.
3. Use `get_task_results(task_id)` to retrieve stdout/stderr/exit code once complete.

For quick commands you can use `run_quick_command()` which blocks until done.

## IMPORTANT: Auto-check running tasks

At the START of every new user message, if there are any tasks that were \
previously started and might still be running, proactively call \
`check_task_status()` for each one before doing anything else. Users expect \
you to keep them updated without being asked.

## Notes

- Commands run via `sh -c`, so pipes, redirects, and shell constructs work.
- Long-running commands heartbeat every 10 seconds so Temporal knows they are alive.
- Tasks survive disconnections and are retried automatically on transient failures.
- For commands expected to run longer than a few seconds, use `start_command()` \
  (non-blocking) rather than `run_quick_command()` to avoid timeouts.
"""

mcp = FastMCP(
    "shell-runner",
    instructions=INSTRUCTIONS,
    host="0.0.0.0",
)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_temporal_client: Client | None = None
_task_registry: dict[str, dict[str, Any]] = {}

TASK_QUEUE = "shell-tasks"
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")
TEMPORAL_CONNECT_RETRIES = 60
TEMPORAL_RETRY_DELAY_SECONDS = 2


async def _get_client() -> Client:
    """Lazy-initialize a single Temporal client connection."""
    global _temporal_client
    if _temporal_client is None:
        last_exc: Exception | None = None
        for attempt in range(1, TEMPORAL_CONNECT_RETRIES + 1):
            try:
                _temporal_client = await Client.connect(TEMPORAL_HOST)
                break
            except Exception as exc:
                last_exc = exc
                if attempt == TEMPORAL_CONNECT_RETRIES:
                    break
                await asyncio.sleep(TEMPORAL_RETRY_DELAY_SECONDS)
        if _temporal_client is None:
            raise RuntimeError(
                f"Failed to connect to Temporal at {TEMPORAL_HOST} "
                f"after {TEMPORAL_CONNECT_RETRIES} attempts"
            ) from last_exc
    return _temporal_client


def _normalize_workflow_status(status_obj: Any) -> str:
    """Normalize Temporal status to plain values like RUNNING/COMPLETED/FAILED."""
    if status_obj is None:
        return "UNKNOWN"
    raw = getattr(status_obj, "name", str(status_obj))
    if raw.startswith("WORKFLOW_EXECUTION_STATUS_"):
        raw = raw.replace("WORKFLOW_EXECUTION_STATUS_", "", 1)
    return raw


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def start_command(
    command: str,
    label: str = "",
) -> dict[str, str]:
    """Start a shell command in the background via a Temporal workflow.

    Returns immediately with a task_id you can use to check status and
    retrieve results later.

    Args:
        command: The shell command to run (executed via ``sh -c``)
        label: Optional human-friendly label for this task
    """
    client = await _get_client()
    task_id = f"task-{uuid.uuid4().hex[:12]}"

    cmd_input = ShellCommandInput(
        command=command,
        task_id=task_id,
    )

    await client.start_workflow(
        ShellCommandWorkflow.run,
        cmd_input,
        id=task_id,
        task_queue=TASK_QUEUE,
    )

    _task_registry[task_id] = {
        "command": command,
        "label": label or command[:60],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "RUNNING",
    }

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "message": (
            f"Command started. Use check_task_status('{task_id}') to poll "
            f"and get_task_results('{task_id}') when complete."
        ),
    }


@mcp.tool()
async def check_task_status(task_id: str) -> dict[str, Any]:
    """Check the current status of a running or completed task.

    Args:
        task_id: The task identifier returned by start_command()
    """
    client = await _get_client()

    handle = client.get_workflow_handle(task_id)
    desc = await handle.describe()

    status_name = _normalize_workflow_status(desc.status)

    if task_id in _task_registry:
        _task_registry[task_id]["status"] = status_name

    result: dict[str, Any] = {
        "task_id": task_id,
        "status": status_name,
    }

    if task_id in _task_registry:
        result["label"] = _task_registry[task_id].get("label", "")
        result["command"] = _task_registry[task_id].get("command", "")
        result["started_at"] = _task_registry[task_id].get("started_at", "")

    if status_name == "COMPLETED":
        result["message"] = (
            f"Task finished! Use get_task_results('{task_id}') to retrieve results."
        )
    elif status_name == "FAILED":
        result["message"] = "Task failed. Check Temporal UI at :8080 for details."
    elif status_name == "RUNNING":
        result["message"] = "Task is still running. Check again shortly."
    else:
        result["message"] = f"Workflow status: {status_name}"

    return result


@mcp.tool()
async def get_task_results(task_id: str) -> dict[str, Any]:
    """Fetch the results of a completed task.

    Returns stdout, stderr, exit code, and a human-readable summary.

    Args:
        task_id: The task identifier returned by start_command()
    """
    client = await _get_client()
    handle = client.get_workflow_handle(task_id)

    desc = await handle.describe()
    status_name = _normalize_workflow_status(desc.status)

    if status_name != "COMPLETED":
        return {
            "task_id": task_id,
            "status": status_name,
            "error": (
                f"Task is not yet complete (status: {status_name}). "
                "Use check_task_status() to poll."
            ),
        }

    result = await handle.result()

    if task_id in _task_registry:
        _task_registry[task_id]["status"] = "COMPLETED"

    return {
        "task_id": task_id,
        "status": "COMPLETED",
        **result,
    }


@mcp.tool()
async def run_quick_command(command: str) -> dict[str, Any]:
    """Run a shell command and wait for the result (blocking).

    Convenience method for short-lived commands. For long-running commands,
    use start_command() instead to avoid timeouts.

    Args:
        command: The shell command to run (executed via ``sh -c``)
    """
    client = await _get_client()
    task_id = f"task-{uuid.uuid4().hex[:12]}"

    cmd_input = ShellCommandInput(
        command=command,
        task_id=task_id,
    )

    _task_registry[task_id] = {
        "command": command,
        "label": command[:60],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "RUNNING",
    }

    result = await client.execute_workflow(
        ShellCommandWorkflow.run,
        cmd_input,
        id=task_id,
        task_queue=TASK_QUEUE,
    )

    _task_registry[task_id]["status"] = "COMPLETED"

    return {
        "task_id": task_id,
        "status": "COMPLETED",
        **result,
    }


@mcp.tool()
async def list_recent_tasks() -> dict[str, Any]:
    """List all tasks started in this session with their current status.

    Useful for keeping track of multiple concurrent tasks.
    """
    client = await _get_client()

    tasks: list[dict[str, Any]] = []
    for task_id, meta in _task_registry.items():
        entry = {"task_id": task_id, **meta}

        # Refresh status from Temporal for non-terminal states
        if meta.get("status") in ("RUNNING", "UNKNOWN"):
            try:
                handle = client.get_workflow_handle(task_id)
                desc = await handle.describe()
                status_name = _normalize_workflow_status(desc.status)
                entry["status"] = status_name
                _task_registry[task_id]["status"] = status_name
            except Exception:
                entry["status"] = "UNKNOWN"

        tasks.append(entry)

    return {
        "total": len(tasks),
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--stdio" in sys.argv:
        # stdio mode for mcpo proxy or direct MCP client
        mcp.run(transport="stdio")
    else:
        # Streamable HTTP mode (default)
        mcp.run(transport="streamable-http")
