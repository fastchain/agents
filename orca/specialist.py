from __future__ import annotations

"""
specialist.py — Resumable Specialist Agent

A specialist:
  - Listens on a bus queue for tasks
  - Has a large manual loaded in its system prompt
  - Processes one task at a time
  - Writes results back to the bus
  - Checkpoints its own state so it can resume after a crash

The specialist is completely stateless between tasks — each task is a
fresh LLM call with the full manual in the system prompt.
The BUS is the source of truth for which tasks exist and their results.

Run a specialist:
    python specialist.py --queue db_specialist --manual manuals/postgres.md

Multiple specialists can run in parallel for the same queue (they compete
to claim tasks — only one wins each task due to atomic DB update).
"""

import time
import uuid
import argparse
from pathlib import Path

import litellm
from rich.console import Console
from rich.panel import Panel

from bus import DurableBus
from config import DEFAULT_MODEL

console = Console()


class SpecialistAgent:
    """
    A long-running worker that:
      1. Reads tasks from a bus queue
      2. Processes each with its manual in the system prompt
      3. Writes results back to the bus
      4. Checkpoints progress for resumability
    """

    def __init__(
        self,
        bus: DurableBus,
        queue: str,
        manual_path: str | Path,
        model: str = DEFAULT_MODEL,
        agent_id: str | None = None,
        poll_interval: float = 1.0,
    ):
        self.bus = bus
        self.queue = queue
        self.model = model
        self.agent_id = agent_id or f"{queue}-{str(uuid.uuid4())[:8]}"
        self.poll_interval = poll_interval
        self._running = False

        # Load the manual once — it lives in every system prompt
        manual_file = Path(manual_path)
        if not manual_file.exists():
            raise FileNotFoundError(f"Manual not found: {manual_path}")
        self.manual = manual_file.read_text(encoding="utf-8")
        self.manual_name = manual_file.stem

        console.print(Panel(
            f"[bold]Queue:[/bold]  {queue}\n"
            f"[bold]Manual:[/bold] {manual_file.name} ({len(self.manual):,} chars)\n"
            f"[bold]Model:[/bold]  {model}\n"
            f"[bold]ID:[/bold]     {self.agent_id}",
            title=f"[yellow]Specialist: {self.manual_name}[/yellow]",
        ))

    def run_forever(self):
        """
        Main loop. Polls the bus for tasks and processes them.
        On restart, stale tasks (from a previous crashed run) are automatically
        requeued by the bus after STALE_TASK_TIMEOUT seconds.
        """
        self._running = True
        console.print(f"[dim]Listening on queue: [bold]{self.queue}[/bold][/dim]")

        while self._running:
            # Check if we have an incomplete task from a previous run
            task = self._resume_incomplete_task()

            if task is None:
                # Claim a fresh task
                task = self.bus.claim_task(self.queue, self.agent_id)

            if task is None:
                # Nothing to do, wait
                time.sleep(self.poll_interval)
                continue

            self._process_task(task)

    def process_once(self) -> bool:
        """Process a single task. Returns True if a task was found and processed."""
        task = self.bus.claim_task(self.queue, self.agent_id)
        if task is None:
            return False
        self._process_task(task)
        return True

    def stop(self):
        self._running = False

    # ── Internal ───────────────────────────────────────────────────────────

    def _process_task(self, task):
        task_desc = task.payload.get("task", str(task.payload))
        console.print(f"\n[bold yellow]⚡ Task {task.id[:8]}:[/bold yellow] {task_desc[:120]}")

        # Save checkpoint: we're working on this task
        self.bus.save_checkpoint(self.agent_id, "current_task_id", task.id)
        self.bus.save_checkpoint(self.agent_id, "current_task_status", "processing")

        try:
            result = self._run_llm(task_desc)

            self.bus.complete_task(task.id, result={"output": result})

            # Clear the checkpoint — task is done
            self.bus.save_checkpoint(self.agent_id, "current_task_id", None)
            self.bus.save_checkpoint(self.agent_id, "current_task_status", "idle")

            console.print(f"[green]✓ Task {task.id[:8]} completed[/green]")
            console.print(f"[dim]  Result: {result[:200]}{'...' if len(result) > 200 else ''}[/dim]")

        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            self.bus.fail_task(task.id, error=error)
            self.bus.save_checkpoint(self.agent_id, "current_task_status", "failed")
            console.print(f"[red]✗ Task {task.id[:8]} failed: {error}[/red]")

    def _run_llm(self, task: str) -> str:
        """Run the LLM with the full manual in system context."""
        system = f"""You are a specialist in: {self.manual_name}.

You have the complete reference manual below. Use it to complete the task precisely.

Return ONLY the result — no preamble, no explanation of what you're doing.
Be concise and specific.

=== MANUAL: {self.manual_name} ===
{self.manual}
=== END MANUAL ===
"""
        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": task},
            ],
            max_tokens=4096,
            temperature=0.0,
        )
        return response.choices[0].message.content

    def _resume_incomplete_task(self):
        """
        On startup, check if we were in the middle of a task when we crashed.
        If so, reclaim it from the bus.

        Note: the bus auto-requeues stale tasks after STALE_TASK_TIMEOUT,
        so by the time we get here on restart it should already be 'pending' again.
        """
        last_task_id = self.bus.load_checkpoint(self.agent_id, "current_task_id")
        last_status  = self.bus.load_checkpoint(self.agent_id, "current_task_status")

        if last_task_id and last_status == "processing":
            task = self.bus.get_task(last_task_id)
            if task and task.status == "pending":  # was requeued after stale timeout
                console.print(f"[yellow]↩ Resuming incomplete task {last_task_id[:8]}[/yellow]")
                return self.bus.claim_task(self.queue, self.agent_id)

        return None


# ── CLI entry point ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run a specialist agent")
    parser.add_argument("--queue",   required=True, help="Bus queue to listen on")
    parser.add_argument("--manual",  required=True, help="Path to the manual .md file")
    parser.add_argument("--model",   default=DEFAULT_MODEL, help="LiteLLM model string")
    parser.add_argument("--db",      default="agent_bus.db", help="SQLite bus database path")
    parser.add_argument("--agent-id", default=None, help="Agent instance ID (auto-generated if not set)")
    args = parser.parse_args()

    bus = DurableBus(args.db)
    specialist = SpecialistAgent(
        bus=bus,
        queue=args.queue,
        manual_path=args.manual,
        model=args.model,
        agent_id=args.agent_id,
    )
    try:
        specialist.run_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Specialist stopped.[/dim]")


if __name__ == "__main__":
    main()
