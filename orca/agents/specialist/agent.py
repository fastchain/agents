from __future__ import annotations

"""
agents/specialist/agent.py — Resumable Specialist Agent
"""

import argparse
import time
import uuid
from pathlib import Path

import litellm
from rich.console import Console
from rich.panel import Panel

from bus import DurableBus
from .config import DEFAULT_MODEL

console = Console()


class SpecialistAgent:
    """Long-running worker that claims tasks from one queue and processes them."""

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

        manual_file = Path(manual_path)
        if not manual_file.exists():
            raise FileNotFoundError(f"Manual not found: {manual_path}")
        self.manual = manual_file.read_text(encoding="utf-8")
        self.manual_name = manual_file.stem

        console.print(
            Panel(
                f"[bold]Queue:[/bold]  {queue}\n"
                f"[bold]Manual:[/bold] {manual_file.name} ({len(self.manual):,} chars)\n"
                f"[bold]Model:[/bold]  {model}\n"
                f"[bold]ID:[/bold]     {self.agent_id}",
                title=f"[yellow]Specialist: {self.manual_name}[/yellow]",
            )
        )

    def run_forever(self):
        self._running = True
        console.print(f"[dim]Listening on queue: [bold]{self.queue}[/bold][/dim]")

        while self._running:
            task = self._resume_incomplete_task()

            if task is None:
                task = self.bus.claim_task(self.queue, self.agent_id)

            if task is None:
                time.sleep(self.poll_interval)
                continue

            self._process_task(task)

    def process_once(self) -> bool:
        task = self.bus.claim_task(self.queue, self.agent_id)
        if task is None:
            return False
        self._process_task(task)
        return True

    def stop(self):
        self._running = False

    def _process_task(self, task):
        task_desc = task.payload.get("task", str(task.payload))
        console.print(f"\n[bold yellow]⚡ Task {task.id[:8]}:[/bold yellow] {task_desc[:120]}")

        self.bus.save_checkpoint(self.agent_id, "current_task_id", task.id)
        self.bus.save_checkpoint(self.agent_id, "current_task_status", "processing")

        try:
            result = self._run_llm(task_desc)

            self.bus.complete_task(task.id, result={"output": result})

            self.bus.save_checkpoint(self.agent_id, "current_task_id", None)
            self.bus.save_checkpoint(self.agent_id, "current_task_status", "idle")

            console.print(f"[green]✓ Task {task.id[:8]} completed[/green]")
            console.print(f"[dim]  Result: {result[:200]}{'...' if len(result) > 200 else ''}[/dim]")

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.bus.fail_task(task.id, error=error)
            self.bus.save_checkpoint(self.agent_id, "current_task_status", "failed")
            console.print(f"[red]✗ Task {task.id[:8]} failed: {error}[/red]")

    def _run_llm(self, task: str) -> str:
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
                {"role": "user", "content": task},
            ],
            max_tokens=4096,
            temperature=0.0,
        )
        return response.choices[0].message.content

    def _resume_incomplete_task(self):
        last_task_id = self.bus.load_checkpoint(self.agent_id, "current_task_id")
        last_status = self.bus.load_checkpoint(self.agent_id, "current_task_status")

        if last_task_id and last_status == "processing":
            task = self.bus.get_task(last_task_id)
            if task and task.status == "pending":
                console.print(f"[yellow]↩ Resuming incomplete task {last_task_id[:8]}[/yellow]")
                return self.bus.claim_task(self.queue, self.agent_id)

        return None


def main():
    parser = argparse.ArgumentParser(description="Run a specialist agent")
    parser.add_argument("--queue", required=True, help="Bus queue to listen on")
    parser.add_argument("--manual", required=True, help="Path to the manual .md file")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LiteLLM model string")
    parser.add_argument("--db", default="agent_bus.db", help="Ignored (backward compatible)")
    parser.add_argument("--irc-host", default="localhost", help="IRC server host")
    parser.add_argument("--irc-port", type=int, default=6667, help="IRC server port")
    parser.add_argument("--agent-id", default=None, help="Agent instance ID (auto-generated if not set)")
    args = parser.parse_args()

    bus = DurableBus(args.db, irc_host=args.irc_host, irc_port=args.irc_port)
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
