"""
orchestrator.py — Resumable Orchestrator

The orchestrator uses the bus for ALL state:
  - Which tasks it created (so it doesn't duplicate them on restart)
  - Where it is in a multi-step plan (checkpoint)
  - Results from specialists

On restart, the orchestrator:
  1. Loads its last checkpoint from the bus
  2. Sees which tasks are already done / in-progress
  3. Continues from where it left off — no work is repeated

Usage:
    python orchestrator.py
    # ^^ if it crashes, just run again — it resumes automatically
"""

import time
import json
import uuid
import threading
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

import litellm

from bus import DurableBus
from config import DEFAULT_MODEL, SKILLS_DIR
from skills import SkillLoader

console = Console()


class ResumableOrchestrator:
    """
    An orchestrator agent that persists its entire execution state to the bus.
    Can be killed and restarted at any time — it always resumes correctly.
    """

    def __init__(
        self,
        bus: DurableBus,
        agent_id: str = "orchestrator",
        model: str = DEFAULT_MODEL,
    ):
        self.bus = bus
        self.agent_id = agent_id
        self.model = model
        self.skill_loader = SkillLoader(SKILLS_DIR)

        # Build the specialist tool definitions for the LLM
        self._specialist_queues: dict[str, str] = {}  # tool_name → queue_name

    def register_specialist(self, tool_name: str, queue: str, description: str):
        """
        Tell the orchestrator about a specialist it can delegate to.

        tool_name:   what the LLM calls (e.g. "ask_database_specialist")
        queue:       bus queue the specialist listens on (e.g. "db_specialist")
        description: what the LLM uses to decide when to delegate
        """
        self._specialist_queues[tool_name] = queue
        console.print(f"[dim]Registered specialist: {tool_name} → queue '{queue}'[/dim]")

    def run(self, session_id: str, initial_task: str) -> str:
        """
        Run (or resume) a session.

        session_id:   unique ID for this work session (you provide it so
                      you can resume the same session after a restart)
        initial_task: the original user request

        Returns the final answer.
        """
        console.print(Panel(
            f"[bold]Session:[/bold] {session_id}\n"
            f"[bold]Task:[/bold] {initial_task}",
            title="[green]Orchestrator[/green]",
        ))

        # ── Load or initialize session state ──────────────────────────────
        state = self._load_state(session_id)

        if state.get("status") == "done":
            console.print("[green]✓ Session already completed.[/green]")
            return state.get("final_answer", "")

        if state.get("status") == "in_progress":
            console.print(f"[yellow]↩ Resuming session from step: {state.get('step', 'start')}[/yellow]")
        else:
            # Fresh session
            state = {
                "session_id": session_id,
                "initial_task": initial_task,
                "status": "in_progress",
                "step": "planning",
                "history": [],
                "pending_task_ids": [],   # task IDs we've published but not yet received results for
                "results": {},             # task_id → result
            }
            self._save_state(session_id, state)

        # ── Main orchestration loop ────────────────────────────────────────
        return self._orchestration_loop(session_id, state)

    def _orchestration_loop(self, session_id: str, state: dict) -> str:
        """
        The core loop. Each iteration:
          1. Collect any results from specialists that have finished
          2. Ask the LLM what to do next
          3. Execute: either delegate to a specialist or return final answer
          4. Save state (so we can resume if we crash here)
        """
        MAX_ITERATIONS = 20

        for iteration in range(MAX_ITERATIONS):
            # ── Step 1: Collect completed specialist results ───────────────
            new_results = self._collect_results(state["pending_task_ids"])
            if new_results:
                state["results"].update(new_results)
                state["pending_task_ids"] = [
                    tid for tid in state["pending_task_ids"] if tid not in new_results
                ]
                # Inject results into history so LLM knows about them
                for task_id, result in new_results.items():
                    state["history"].append({
                        "role": "tool",
                        "content": f"[Task {task_id} completed]\n{json.dumps(result, indent=2)}",
                    })
                self._save_state(session_id, state)

            # ── Step 2: If we're still waiting for specialists, poll ───────
            if state["pending_task_ids"]:
                console.print(f"[dim]Waiting for {len(state['pending_task_ids'])} specialist(s)...[/dim]")
                time.sleep(2)
                continue

            # ── Step 3: Ask the LLM what to do next ───────────────────────
            state["step"] = f"iteration_{iteration}"
            self._save_state(session_id, state)

            llm_response = self._llm_call(state["initial_task"], state["history"])

            # ── Step 4: Parse response ─────────────────────────────────────
            # We ask the LLM to respond in structured JSON so we can parse it reliably
            parsed = self._parse_llm_response(llm_response)

            if parsed["action"] == "final_answer":
                # Done!
                answer = parsed["content"]
                state["status"] = "done"
                state["final_answer"] = answer
                self._save_state(session_id, state)
                console.print(f"\n[bold cyan]Final Answer:[/bold cyan]\n{answer}")
                return answer

            elif parsed["action"] == "delegate":
                # Delegate tasks to specialists
                delegations = parsed.get("tasks", [])
                for delegation in delegations:
                    tool_name = delegation["specialist"]
                    task_payload = delegation["task"]
                    queue = self._specialist_queues.get(tool_name)

                    if not queue:
                        console.print(f"[red]Unknown specialist: {tool_name}[/red]")
                        continue

                    task_id = self.bus.publish_task(queue, {
                        "task": task_payload,
                        "session_id": session_id,
                    })
                    state["pending_task_ids"].append(task_id)
                    console.print(f"[yellow]→ Delegated to {tool_name} (task: {task_id[:8]}...)[/yellow]")
                    console.print(f"  [dim]{task_payload[:100]}[/dim]")

                # Add to history
                state["history"].append({
                    "role": "assistant",
                    "content": f"Delegated {len(delegations)} task(s) to specialists.",
                })
                self._save_state(session_id, state)

            else:
                # LLM is thinking / adding to context
                content = parsed.get("content", llm_response)
                state["history"].append({"role": "assistant", "content": content})
                self._save_state(session_id, state)

        return "[max iterations reached]"

    def _llm_call(self, initial_task: str, history: list[dict]) -> str:
        """Ask the LLM what to do next, given the current state."""
        specialists_desc = "\n".join(
            f"  - {name}: delegates to the '{queue}' specialist"
            for name, queue in self._specialist_queues.items()
        )

        system = f"""You are an orchestrator agent. You break down complex tasks and delegate to specialists.

Available specialists:
{specialists_desc}

Respond ONLY with a JSON object in one of these formats:

To delegate work:
{{
  "action": "delegate",
  "tasks": [
    {{"specialist": "<tool_name>", "task": "<clear task description for the specialist>"}},
    ...
  ]
}}

To provide the final answer (when all work is done):
{{
  "action": "final_answer",
  "content": "<your complete response to the user>"
}}

Be specific in task descriptions — specialists only receive the task string, not the full conversation.
"""
        messages = [{"role": "system", "content": system}]

        # Add task as first user message if history is empty
        if not history:
            messages.append({"role": "user", "content": initial_task})
        else:
            messages.append({"role": "user", "content": initial_task})
            messages.extend(history)

        response = litellm.completion(
            model=self.model,
            messages=messages,
            max_tokens=2048,
            temperature=0.0,
        )
        return response.choices[0].message.content

    def _parse_llm_response(self, raw: str) -> dict:
        """Parse JSON from LLM response, with fallback."""
        try:
            # Strip markdown code fences if present
            clean = raw.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = "\n".join(clean.split("\n")[:-1])
            return json.loads(clean.strip())
        except Exception:
            # If parsing fails, treat as a thinking step
            return {"action": "think", "content": raw}

    def _collect_results(self, pending_task_ids: list[str]) -> dict:
        """Check which pending tasks are done and collect their results."""
        results = {}
        for task_id in pending_task_ids:
            task = self.bus.get_task(task_id)
            if task and task.status == "done":
                results[task_id] = task.result
            elif task and task.status == "failed":
                results[task_id] = {"error": task.error}
        return results

    def _load_state(self, session_id: str) -> dict:
        return self.bus.load_checkpoint(self.agent_id, f"session_{session_id}", default={})

    def _save_state(self, session_id: str, state: dict):
        self.bus.save_checkpoint(self.agent_id, f"session_{session_id}", state)
