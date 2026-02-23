"""
bus_inspector.py — CLI tool to inspect the bus database

Usage:
    python bus_inspector.py                    # show all queues
    python bus_inspector.py tasks              # list all tasks
    python bus_inspector.py tasks --queue db_specialist
    python bus_inspector.py task <task_id>     # show a specific task
    python bus_inspector.py checkpoints        # show all checkpoints
    python bus_inspector.py checkpoints --agent orchestrator
    python bus_inspector.py messages           # show all bus messages
"""

import argparse
import json
from rich.console import Console
from rich.table import Table

from bus import DurableBus

console = Console()


def cmd_status(bus: DurableBus, args):
    report = bus.status_report()
    if not report:
        console.print("[dim]Bus is empty.[/dim]")
        return

    table = Table(title="Bus Status", show_header=True)
    table.add_column("Queue", style="cyan")
    table.add_column("Pending",    style="yellow", justify="right")
    table.add_column("Processing", style="blue",   justify="right")
    table.add_column("Done",       style="green",  justify="right")
    table.add_column("Failed",     style="red",    justify="right")

    for queue, counts in sorted(report.items()):
        table.add_row(
            queue,
            str(counts.get("pending", 0)),
            str(counts.get("processing", 0)),
            str(counts.get("done", 0)),
            str(counts.get("failed", 0)),
        )
    console.print(table)


def cmd_tasks(bus: DurableBus, args):
    queue_filter = getattr(args, "queue", None)
    status_filter = getattr(args, "status", None)

    conn = bus._conn
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if queue_filter:
        query += " AND queue=?"
        params.append(queue_filter)
    if status_filter:
        query += " AND status=?"
        params.append(status_filter)
    query += " ORDER BY created_at DESC LIMIT 50"

    rows = conn.execute(query, params).fetchall()

    table = Table(title="Tasks", show_header=True)
    table.add_column("ID",      style="dim",    width=10)
    table.add_column("Queue",   style="cyan")
    table.add_column("Status",  style="yellow")
    table.add_column("Retries", justify="right")
    table.add_column("Agent",   style="dim",    width=20)
    table.add_column("Task (truncated)")

    status_colors = {"pending": "yellow", "processing": "blue", "done": "green", "failed": "red"}

    for row in rows:
        payload = json.loads(row["payload"])
        task_preview = str(payload.get("task", payload))[:60]
        status = row["status"]
        color = status_colors.get(status, "white")

        table.add_row(
            row["id"][:8] + "...",
            row["queue"],
            f"[{color}]{status}[/{color}]",
            str(row["retries"]),
            (row["claimed_by"] or "")[:20],
            task_preview,
        )
    console.print(table)


def cmd_task_detail(bus: DurableBus, args):
    task = bus.get_task(args.task_id)
    if not task:
        # Try prefix match
        conn = bus._conn
        row = conn.execute("SELECT id FROM tasks WHERE id LIKE ?", (args.task_id + "%",)).fetchone()
        if row:
            task = bus.get_task(row["id"])

    if not task:
        console.print(f"[red]Task not found: {args.task_id}[/red]")
        return

    console.print(f"\n[bold]Task {task.id}[/bold]")
    console.print(f"  Queue:      {task.queue}")
    console.print(f"  Status:     {task.status}")
    console.print(f"  Claimed by: {task.claimed_by}")
    console.print(f"  Retries:    {task.retries}/{task.max_retries}")
    console.print(f"\n[bold]Payload:[/bold]")
    console.print(json.dumps(task.payload, indent=2))
    if task.result:
        console.print(f"\n[bold green]Result:[/bold green]")
        console.print(json.dumps(task.result, indent=2))
    if task.error:
        console.print(f"\n[bold red]Error:[/bold red] {task.error}")


def cmd_checkpoints(bus: DurableBus, args):
    agent_filter = getattr(args, "agent", None)
    conn = bus._conn

    query = "SELECT * FROM checkpoints"
    params = []
    if agent_filter:
        query += " WHERE agent_id=?"
        params.append(agent_filter)
    query += " ORDER BY agent_id, key"

    rows = conn.execute(query, params).fetchall()

    table = Table(title="Checkpoints", show_header=True)
    table.add_column("Agent",   style="cyan")
    table.add_column("Key",     style="yellow")
    table.add_column("Value (truncated)")

    for row in rows:
        value = row["value"]
        if len(value) > 80:
            value = value[:80] + "..."
        table.add_row(row["agent_id"], row["key"], value)

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Inspect the agent bus database")
    parser.add_argument("--db", default="agent_bus.db", help="SQLite database path")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    p_tasks = sub.add_parser("tasks")
    p_tasks.add_argument("--queue",  help="Filter by queue name")
    p_tasks.add_argument("--status", help="Filter by status (pending/processing/done/failed)")

    p_task = sub.add_parser("task")
    p_task.add_argument("task_id", help="Task ID (or prefix)")

    p_ckpt = sub.add_parser("checkpoints")
    p_ckpt.add_argument("--agent", help="Filter by agent ID")

    args = parser.parse_args()
    bus = DurableBus(args.db)

    if args.command == "tasks":
        cmd_tasks(bus, args)
    elif args.command == "task":
        cmd_task_detail(bus, args)
    elif args.command == "checkpoints":
        cmd_checkpoints(bus, args)
    else:
        cmd_status(bus, args)


if __name__ == "__main__":
    main()
