"""
mcp/orchestrator/bus_dashboard_server.py — MCP server exposing bus state & control

An MCP (Model Context Protocol) server that gives LLM-based tools read/write
access to the DurableBus SQLite database. Useful for building dashboards,
debugging agent state, or letting an orchestrator LLM inspect the bus directly.

Run:
    BUS_DB_PATH=agent_bus.db python mcp/orchestrator/bus_dashboard_server.py

Tools exposed:
    - bus_status       → Queue overview (pending/processing/done/failed counts)
    - get_task         → Fetch a single task by ID
    - list_tasks       → List tasks with optional queue/status filters
    - list_checkpoints → Show agent checkpoints
    - submit_task      → Publish a new task to a queue
    - get_session_state → Load orchestrator session state from checkpoints
"""

from __future__ import annotations

import json
import os
import sys

# Add project root to path so we can import bus
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bus import DurableBus
from mcp.server.fastmcp import FastMCP

# ── Server setup ───────────────────────────────────────────────────────────

DB_PATH = os.environ.get("BUS_DB_PATH", "agent_bus.db")

mcp = FastMCP("Bus Dashboard")
bus = DurableBus(DB_PATH)


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
    conn = bus._conn
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []

    if queue is not None:
        query += " AND queue = ?"
        params.append(queue)
    if status is not None:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "queue": row["queue"],
            "status": row["status"],
            "created_at": row["created_at"],
            "payload_preview": json.loads(row["payload"]).get("task", "")[:100],
            "error": row["error"],
        })
    return results


@mcp.tool()
def list_checkpoints(agent_id: str | None = None) -> list[dict]:
    """List agent checkpoints, optionally filtered by agent ID.

    Args:
        agent_id: Filter by agent ID (optional). If omitted, shows all agents.

    Returns a list of checkpoint records.
    """
    conn = bus._conn
    if agent_id is not None:
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE agent_id = ? ORDER BY saved_at DESC",
            (agent_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM checkpoints ORDER BY agent_id, saved_at DESC"
        ).fetchall()

    return [
        {
            "agent_id": row["agent_id"],
            "key": row["key"],
            "value": json.loads(row["value"]),
            "saved_at": row["saved_at"],
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
