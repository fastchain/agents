"""
agents/orchestrator/agent.py — Resumable Orchestrator
"""

import json
import time

import litellm
from rich.console import Console
from rich.panel import Panel

from bus import DurableBus
from .config import DEFAULT_MODEL

console = Console()


class ResumableOrchestrator:
    """
    An orchestrator agent that persists its entire execution state to the bus.
    Can be killed and restarted at any time and always resumes correctly.
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
        self._specialist_queues: dict[str, str] = {}

    def register_specialist(self, tool_name: str, queue: str, description: str):
        """Register a specialist callable by `tool_name` backed by `queue`."""
        _ = description
        self._specialist_queues[tool_name] = queue
        console.print(f"[dim]Registered specialist: {tool_name} -> queue '{queue}'[/dim]")

    def run(self, session_id: str, initial_task: str) -> str:
        console.print(
            Panel(
                f"[bold]Session:[/bold] {session_id}\n"
                f"[bold]Task:[/bold] {initial_task}",
                title="[green]Orchestrator[/green]",
            )
        )

        state = self._load_state(session_id)

        if state.get("status") == "done":
            console.print("[green]✓ Session already completed.[/green]")
            return state.get("final_answer", "")

        if state.get("status") == "in_progress":
            console.print(f"[yellow]↩ Resuming session from step: {state.get('step', 'start')}[/yellow]")
        else:
            state = {
                "session_id": session_id,
                "initial_task": initial_task,
                "status": "in_progress",
                "step": "planning",
                "history": [],
                "pending_task_ids": [],
                "results": {},
            }
            self._save_state(session_id, state)

        return self._orchestration_loop(session_id, state)

    def _orchestration_loop(self, session_id: str, state: dict) -> str:
        max_iterations = 20

        for iteration in range(max_iterations):
            new_results = self._collect_results(state["pending_task_ids"])
            if new_results:
                state["results"].update(new_results)
                state["pending_task_ids"] = [
                    task_id for task_id in state["pending_task_ids"] if task_id not in new_results
                ]
                for task_id, result in new_results.items():
                    state["history"].append(
                        {
                            "role": "assistant",
                            "content": f"[Task {task_id} completed]\n{json.dumps(result, indent=2)}",
                        }
                    )
                self._save_state(session_id, state)

            if state["pending_task_ids"]:
                console.print(f"[dim]Waiting for {len(state['pending_task_ids'])} specialist(s)...[/dim]")
                time.sleep(2)
                continue

            state["step"] = f"iteration_{iteration}"
            self._save_state(session_id, state)

            llm_response = self._llm_call(state["initial_task"], state["history"])
            parsed = self._parse_llm_response(llm_response)

            if parsed["action"] == "final_answer":
                answer = parsed["content"]
                state["status"] = "done"
                state["final_answer"] = answer
                self._save_state(session_id, state)
                console.print(f"\n[bold cyan]Final Answer:[/bold cyan]\n{answer}")
                return answer

            if parsed["action"] == "delegate":
                delegations = parsed.get("tasks", [])
                for delegation in delegations:
                    tool_name = delegation["specialist"]
                    task_payload = delegation["task"]
                    queue = self._specialist_queues.get(tool_name)

                    if not queue:
                        console.print(f"[red]Unknown specialist: {tool_name}[/red]")
                        continue

                    task_id = self.bus.publish_task(
                        queue,
                        {
                            "task": task_payload,
                            "session_id": session_id,
                        },
                    )
                    state["pending_task_ids"].append(task_id)
                    console.print(f"[yellow]→ Delegated to {tool_name} (task: {task_id[:8]}...)[/yellow]")
                    console.print(f"  [dim]{task_payload[:100]}[/dim]")

                state["history"].append(
                    {
                        "role": "assistant",
                        "content": f"Delegated {len(delegations)} task(s) to specialists.",
                    }
                )
                self._save_state(session_id, state)
                continue

            content = parsed.get("content", llm_response)
            state["history"].append({"role": "assistant", "content": content})
            self._save_state(session_id, state)

        return "[max iterations reached]"

    def _llm_call(self, initial_task: str, history: list[dict]) -> str:
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
        messages = [{"role": "system", "content": system}, {"role": "user", "content": initial_task}]

        if history:
            messages.extend(self._normalize_history(history))

        response = litellm.completion(
            model=self.model,
            messages=messages,
            max_tokens=2048,
            temperature=0.0,
        )
        return response.choices[0].message.content

    def _normalize_history(self, history: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for msg in history:
            role = msg.get("role", "assistant")
            content = msg.get("content", "")
            if role not in {"system", "user", "assistant"}:
                role = "assistant"
            normalized.append({"role": role, "content": content})
        return normalized

    def _parse_llm_response(self, raw: str) -> dict:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = "\n".join(clean.split("\n")[:-1])
            return json.loads(clean.strip())
        except Exception:
            return {"action": "think", "content": raw}

    def _collect_results(self, pending_task_ids: list[str]) -> dict:
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
