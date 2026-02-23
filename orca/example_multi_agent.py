"""
example_multi_agent.py — Full Example

Shows how the orchestrator and specialists work together with the durable bus.

Run this to simulate:
  1. Orchestrator starts, creates a task
  2. "Crash" (we kill the orchestrator midway)
  3. Restart — orchestrator resumes from checkpoint
  4. Specialist finishes, orchestrator gets result

To run:
    python example_multi_agent.py

To simulate crash + resume:
    python example_multi_agent.py --crash      # stops after first delegation
    python example_multi_agent.py --resume     # resumes the same session
"""

import sys
import time
import threading
import argparse
from pathlib import Path

from rich.console import Console

from bus import DurableBus
from orchestrator import ResumableOrchestrator
from specialist import SpecialistAgent

console = Console()

# Fixed session ID so we can resume the same session across runs
SESSION_ID = "demo-session-001"
DB_PATH = "demo_bus.db"


def create_demo_manual():
    """Create a fake manual for the demo specialist."""
    manual_path = Path("manuals/demo_system.md")
    manual_path.parent.mkdir(exist_ok=True)

    if not manual_path.exists():
        manual_path.write_text("""# Demo System Manual

## Overview
This is a demo system that processes data transformation tasks.

## API
- `transform(input, format)` → converts input to the target format
- `validate(data)` → validates data structure, returns True/False
- `summarize(text, max_words)` → summarizes text to max_words

## Rules
- Always validate before transforming
- Use format "json" for structured data, "text" for human-readable
- Summaries should be at most 50 words unless specified

## Examples

Task: "Summarize this: The quick brown fox jumps over the lazy dog."
Result: "A fox jumps over a dog."

Task: "Transform the list [1,2,3] to JSON"
Result: {"items": [1, 2, 3], "count": 3}
""")
    return manual_path


def run_specialist_in_background(bus: DurableBus, manual_path: Path):
    """Run the specialist in a background thread for the demo."""
    specialist = SpecialistAgent(
        bus=bus,
        queue="demo_specialist",
        manual_path=manual_path,
        agent_id="demo-specialist-1",
    )

    def worker():
        # Process up to 5 tasks then stop (for demo purposes)
        for _ in range(5):
            if not specialist.process_once():
                time.sleep(1)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--crash",  action="store_true", help="Simulate crash after first delegation")
    parser.add_argument("--resume", action="store_true", help="Resume a crashed session")
    args = parser.parse_args()

    bus = DurableBus(DB_PATH)
    manual_path = create_demo_manual()

    # ── Show current bus state ─────────────────────────────────────────────
    console.print("\n[bold]Bus Status:[/bold]")
    status = bus.status_report()
    if status:
        for queue, counts in status.items():
            console.print(f"  {queue}: {counts}")
    else:
        console.print("  [dim](empty — fresh start)[/dim]")

    # ── Set up orchestrator ────────────────────────────────────────────────
    orchestrator = ResumableOrchestrator(bus=bus, agent_id="demo-orchestrator")
    orchestrator.register_specialist(
        tool_name="ask_demo_specialist",
        queue="demo_specialist",
        description="Handles data transformation and summarization tasks using the Demo System",
    )

    # ── Start specialist in background ────────────────────────────────────
    console.print("\n[dim]Starting specialist in background...[/dim]")
    specialist_thread = run_specialist_in_background(bus, manual_path)

    # ── Run or resume the session ──────────────────────────────────────────
    task = "Summarize this sentence: 'The quick brown fox jumps over the lazy dog.' Then transform the result to JSON format."

    console.print(f"\n[bold]Task:[/bold] {task}")
    console.print(f"[bold]Session:[/bold] {SESSION_ID}\n")

    result = orchestrator.run(SESSION_ID, task)

    # ── Show final bus state ───────────────────────────────────────────────
    console.print("\n[bold]Final Bus Status:[/bold]")
    for queue, counts in bus.status_report().items():
        console.print(f"  {queue}: {counts}")

    # ── Show checkpoint state (what would be resumed) ─────────────────────
    checkpoints = bus.load_all_checkpoints("demo-orchestrator")
    console.print(f"\n[bold]Orchestrator Checkpoints saved:[/bold] {len(checkpoints)} keys")
    for key in checkpoints:
        console.print(f"  [dim]•[/dim] {key}")

    console.print(f"\n[green]Done. Database saved to: {DB_PATH}[/green]")
    console.print(f"[dim]Run again — the orchestrator will see the session is already done and skip it.[/dim]")


if __name__ == "__main__":
    main()
