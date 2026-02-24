"""
bus_inspector.py — inspect Mattermost bus state
"""

import argparse
import json
import os

from rich.console import Console
from rich.table import Table

from bus import DurableBus

console = Console()


def cmd_status(bus: DurableBus, _args):
    report = bus.status_report()
    if not report:
        console.print("[dim]No tasks observed yet on this Mattermost bus connection.[/dim]")
        return

    table = Table(title="Bus Status", show_header=True)
    table.add_column("Queue", style="cyan")
    table.add_column("Pending", style="yellow", justify="right")
    table.add_column("Processing", style="blue", justify="right")
    table.add_column("Done", style="green", justify="right")
    table.add_column("Failed", style="red", justify="right")

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
    tasks = bus.list_tasks(queue=args.queue, status=args.status, limit=50)

    table = Table(title="Tasks", show_header=True)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Queue", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("Retries", justify="right")
    table.add_column("Agent", style="dim", width=20)
    table.add_column("Task (truncated)")

    status_colors = {"pending": "yellow", "processing": "blue", "done": "green", "failed": "red"}

    for task in tasks:
        task_preview = str(task.payload.get("task", task.payload))[:60]
        color = status_colors.get(task.status, "white")
        table.add_row(
            task.id[:8] + "...",
            task.queue,
            f"[{color}]{task.status}[/{color}]",
            f"{task.retries}/{task.max_retries}",
            (task.claimed_by or "")[:20],
            task_preview,
        )
    console.print(table)


def cmd_task_detail(bus: DurableBus, args):
    task = bus.get_task(args.task_id)

    if not task:
        for candidate in bus.list_tasks(limit=1000):
            if candidate.id.startswith(args.task_id):
                task = candidate
                break

    if not task:
        console.print(f"[red]Task not found: {args.task_id}[/red]")
        return

    console.print(f"\n[bold]Task {task.id}[/bold]")
    console.print(f"  Queue:      {task.queue}")
    console.print(f"  Status:     {task.status}")
    console.print(f"  Claimed by: {task.claimed_by}")
    console.print(f"  Retries:    {task.retries}/{task.max_retries}")
    console.print("\n[bold]Payload:[/bold]")
    console.print(json.dumps(task.payload, indent=2))
    if task.result:
        console.print("\n[bold green]Result:[/bold green]")
        console.print(json.dumps(task.result, indent=2))
    if task.error:
        console.print(f"\n[bold red]Error:[/bold red] {task.error}")


def cmd_checkpoints(bus: DurableBus, args):
    checkpoints = bus.list_checkpoints(agent_id=args.agent)

    table = Table(title="Checkpoints", show_header=True)
    table.add_column("Agent", style="cyan")
    table.add_column("Key", style="yellow")
    table.add_column("Value (truncated)")

    for cp in checkpoints:
        value = json.dumps(cp.value)
        if len(value) > 80:
            value = value[:80] + "..."
        table.add_row(cp.agent_id, cp.key, value)

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Inspect Mattermost bus state")
    parser.add_argument("--db", default=None, help="Ignored (backward compatible)")
    parser.add_argument(
        "--mm-url",
        default=os.environ.get("MM_URL", "http://localhost:8065"),
        help="Mattermost server URL (env: MM_URL)",
    )
    parser.add_argument(
        "--mm-token",
        default=os.environ.get("MM_TOKEN", ""),
        help="Mattermost bot/personal-access token (env: MM_TOKEN)",
    )
    parser.add_argument(
        "--mm-team",
        default=os.environ.get("MM_TEAM", "agents"),
        help="Mattermost team name (env: MM_TEAM)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    p_tasks = sub.add_parser("tasks")
    p_tasks.add_argument("--queue", help="Filter by queue name")
    p_tasks.add_argument("--status", help="Filter by status (pending/processing/done/failed)")

    p_task = sub.add_parser("task")
    p_task.add_argument("task_id", help="Task ID (or prefix)")

    p_ckpt = sub.add_parser("checkpoints")
    p_ckpt.add_argument("--agent", help="Filter by agent ID")

    args = parser.parse_args()
    bus = DurableBus(mm_url=args.mm_url, mm_token=args.mm_token, mm_team=args.mm_team)

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
