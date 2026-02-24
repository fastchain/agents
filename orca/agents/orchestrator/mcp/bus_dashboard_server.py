"""MCP server exposing bus state and control for the orchestrator."""

from __future__ import annotations

import os

from bus import DurableBus
from mcp.server.fastmcp import FastMCP

MM_URL = os.environ.get("MM_URL", "http://localhost:8065")
MM_TOKEN = os.environ.get("MM_TOKEN", "")
MM_TEAM = os.environ.get("MM_TEAM", "agents")

mcp = FastMCP("Bus Dashboard")
bus = DurableBus(mm_url=MM_URL, mm_token=MM_TOKEN, mm_team=MM_TEAM)


@mcp.tool()
def bus_status() -> dict:
    return bus.status_report()


@mcp.tool()
def get_task(task_id: str) -> dict | str:
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
    task_id = bus.publish_task(
        queue=queue,
        payload={"task": task_description},
        parent_task_id=parent_task_id,
    )
    return {"task_id": task_id, "queue": queue, "status": "pending"}


@mcp.tool()
def get_session_state(session_id: str) -> dict | str:
    state = bus.load_checkpoint("orchestrator", f"session_{session_id}")
    if state is None:
        return f"No session state found for '{session_id}'"
    return state


def main():
    mcp.run()


if __name__ == "__main__":
    main()
