"""
mcp/orchestrator/bus_dashboard_server.py — MCP server exposing bus state & control

An MCP (Model Context Protocol) server that gives LLM-based tools read/write
access to the DurableBus. Useful for building dashboards, debugging agent
state, or letting an orchestrator LLM inspect the bus directly.

Run:
    MM_URL=http://localhost:8065 MM_TOKEN=<token> python mcp/orchestrator/bus_dashboard_server.py

Tools exposed:
    - bus_status       → Queue overview (pending/processing/done/failed counts)
    - get_task         → Fetch a single task by ID
    - list_tasks       → List tasks with optional queue/status filters
    - list_checkpoints → Show agent checkpoints
    - submit_task      → Publish a new task to a queue
    - get_session_state → Load orchestrator session state from checkpoints
"""

from __future__ import annotations

import os
import sys

# Add project root to path so we can import bus
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bus import DurableBus
from mcp.server.fastmcp import FastMCP

# ── Server setup ───────────────────────────────────────────────────────────

MM_URL = os.environ.get("MM_URL", "http://localhost:8065")
MM_TOKEN = os.environ.get("MM_TOKEN", "")
MM_TEAM = os.environ.get("MM_TEAM", "agents")

mcp = FastMCP("Bus Dashboard")
bus = DurableBus(mm_url=MM_URL, mm_token=MM_TOKEN, mm_team=MM_TEAM)


# ── Tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def bus_status() -> dict:
    """Get an overview of all queues and task statuses.

    Returns a dict mapping queue names to status counts, e.g.:
    {"db_specialist": {"pending": 2, "done": 5}, ...}
    """
    return bus.status_report()


@mcp.tool()
def get_task(task_id: str) -> dict | str:
    """Fetch a single task by its ID.

    Args:
        task_id: The unique task identifier.

    Returns the full task record or an error message if not found.
    """
    task = bus.get_task(task_id)
    if task is None:
        return f"Task '{task_id}' not found"
    return {
        "id": task.id,
        "queue": task.queue,
        "payload": task.payload,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "claimed_by": task.claimed_by,
        "result": task.result,
        "error": task.error,
        "retries": task.retries,
        "max_retries": task.max_retries,
        "parent_task_id": task.parent_task_id,
    }


@mcp.tool()
def list_tasks(queue: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    """List tasks with optional queue and status filters.

    Args:
        queue: Filter by queue name (optional).
        status: Filter by status: pending, processing, done, failed (optional).
        limit: Maximum number of tasks to return (default 50).

    Returns a list of task summaries.
    """
    rows = bus.list_tasks(queue=queue, status=status, limit=limit)
    return [
        {
            "id": t.id,
            "queue": t.queue,
            "status": t.status,
            "created_at": t.created_at,
            "payload_preview": str(t.payload.get("task", ""))[:100],
            "error": t.error,
        }
        for t in rows
    ]


@mcp.tool()
def list_checkpoints(agent_id: str | None = None) -> list[dict]:
    """List agent checkpoints, optionally filtered by agent ID.

    Args:
        agent_id: Filter by agent ID (optional). If omitted, shows all agents.

    Returns a list of checkpoint records.
    """
    rows = bus.list_checkpoints(agent_id=agent_id)
    return [
        {
            "agent_id": row.agent_id,
            "key": row.key,
            "value": row.value,
            "saved_at": row.saved_at,
        }
        for row in rows
    ]


@mcp.tool()
def submit_task(queue: str, task_description: str, parent_task_id: str | None = None) -> dict:
    """Submit a new task to a specialist queue.

    Args:
        queue: The specialist queue to publish to (e.g. "db_specialist").
        task_description: Human-readable description of what the specialist should do.
        parent_task_id: Optional parent task ID for subtask trees.

    Returns the created task ID.
    """
    task_id = bus.publish_task(
        queue=queue,
        payload={"task": task_description},
        parent_task_id=parent_task_id,
    )
    return {"task_id": task_id, "queue": queue, "status": "pending"}


@mcp.tool()
def get_session_state(session_id: str) -> dict | str:
    """Load the orchestrator's session state from checkpoints.

    Args:
        session_id: The orchestrator session ID (used as checkpoint key).

    Returns the session state dict, or a message if no state is found.
    """
    state = bus.load_checkpoint("orchestrator", f"session_{session_id}")
    if state is None:
        return f"No session state found for '{session_id}'"
    return state


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
