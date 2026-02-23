"""
tools/orchestrator/task_planner.py — Structured task planning & dependency ordering

Analyzes a high-level task and produces an ExecutionPlan: an ordered sequence of
PlanSteps with dependency edges, grouped into parallel execution waves.

Usage:
    from core import DurableBus
    from tools.orchestrator import TaskPlanner

    bus = DurableBus("my_agents.db")
    specialists = {
        "db_specialist": "Handles PostgreSQL database tasks",
        "api_specialist": "Builds REST API endpoints",
    }

    planner = TaskPlanner(bus, specialists)
    plan = planner.analyze("Build a user registration system")
    print(plan.to_prompt_context())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections import defaultdict, deque


# ── Data classes ───────────────────────────────────────────────────────────


@dataclass
class PlanStep:
    """A single step in an execution plan."""

    id: str                          # unique step identifier (e.g. "step_1")
    specialist: str                  # queue / specialist name
    task_description: str            # what to do
    depends_on: list[str] = field(default_factory=list)  # step IDs this depends on

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "specialist": self.specialist,
            "task_description": self.task_description,
            "depends_on": self.depends_on,
        }


@dataclass
class ExecutionPlan:
    """
    An ordered execution plan with dependency-aware parallel grouping.

    Steps are organized into waves — all steps within a wave can execute
    in parallel because their dependencies are satisfied by prior waves.
    """

    task: str                        # the original high-level task
    steps: list[PlanStep] = field(default_factory=list)
    parallel_waves: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "steps": [s.to_dict() for s in self.steps],
            "parallel_waves": self.parallel_waves,
        }

    def to_prompt_context(self) -> str:
        """Format the plan as text suitable for injection into an LLM prompt."""
        lines = [f"## Execution Plan for: {self.task}", ""]

        for wave_idx, wave in enumerate(self.parallel_waves, 1):
            lines.append(f"### Wave {wave_idx} (parallel)")
            step_map = {s.id: s for s in self.steps}
            for step_id in wave:
                step = step_map[step_id]
                deps = f" (after {', '.join(step.depends_on)})" if step.depends_on else ""
                lines.append(f"  - [{step.id}] → {step.specialist}: {step.task_description}{deps}")
            lines.append("")

        lines.append(f"Total steps: {len(self.steps)}, Waves: {len(self.parallel_waves)}")
        return "\n".join(lines)


# ── Planner ────────────────────────────────────────────────────────────────


class TaskPlanner:
    """
    Analyzes a high-level task and produces a structured ExecutionPlan.

    The planner:
      1. Checks current bus queue load to inform scheduling decisions
      2. Uses an LLM to decompose the task into specialist steps
      3. Topologically sorts steps into parallel execution waves
    """

    def __init__(self, bus, specialist_registry: dict[str, str]):
        """
        Args:
            bus: A DurableBus instance (used to check queue load).
            specialist_registry: Mapping of specialist queue names to descriptions.
                Example: {"db_specialist": "Handles PostgreSQL tasks"}
        """
        self.bus = bus
        self.specialist_registry = specialist_registry

    def analyze(self, task: str) -> ExecutionPlan:
        """
        Analyze a task and produce an execution plan.

        This method builds the LLM prompt, calls the LLM for decomposition,
        parses the response, and computes parallel waves via topological sort.

        Args:
            task: A high-level task description.

        Returns:
            An ExecutionPlan with steps and parallel_waves populated.
        """
        queue_load = self._get_queue_load()
        prompt = self._build_prompt(task, queue_load)
        raw_steps = self._call_llm(prompt)
        steps = self._parse_steps(raw_steps)
        waves = self._topological_waves(steps)
        return ExecutionPlan(task=task, steps=steps, parallel_waves=waves)

    def _get_queue_load(self) -> dict[str, int]:
        """Check pending task count for each specialist queue."""
        load = {}
        for queue in self.specialist_registry:
            load[queue] = self.bus.pending_count(queue)
        return load

    def _build_prompt(self, task: str, queue_load: dict[str, int]) -> str:
        """Build the LLM prompt for task decomposition."""
        specialist_lines = []
        for name, description in self.specialist_registry.items():
            pending = queue_load.get(name, 0)
            specialist_lines.append(f"  - {name}: {description} (pending tasks: {pending})")

        return (
            "You are a task planning assistant. Break down the following task into "
            "concrete steps that can be delegated to the available specialists.\n\n"
            f"Task: {task}\n\n"
            "Available specialists:\n"
            + "\n".join(specialist_lines)
            + "\n\n"
            "Respond with a JSON array of steps. Each step has:\n"
            '  - "id": a unique identifier like "step_1", "step_2", etc.\n'
            '  - "specialist": the specialist queue name to handle this step\n'
            '  - "task_description": what the specialist should do\n'
            '  - "depends_on": array of step IDs that must complete first (empty if none)\n\n'
            "Prefer parallelism where possible. Only add dependencies when a step truly "
            "requires the output of a prior step.\n\n"
            "Respond with ONLY the JSON array, no other text."
        )

    def _call_llm(self, prompt: str) -> list[dict]:
        """
        Call the LLM to decompose a task into steps.

        Uses litellm for model-agnostic LLM calls. Falls back to a
        basic single-step plan if the LLM is unavailable.
        """
        try:
            import litellm
            from config import DEFAULT_MODEL

            response = litellm.completion(
                model=DEFAULT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=2048,
            )
            content = response.choices[0].message.content.strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                content = content.rsplit("```", 1)[0]
            return json.loads(content)
        except Exception:
            # Fallback: return the task as a single step to the first specialist
            first_specialist = next(iter(self.specialist_registry), "default")
            return [
                {
                    "id": "step_1",
                    "specialist": first_specialist,
                    "task_description": f"Complete the following task: {prompt.split('Task: ')[1].split(chr(10))[0]}",
                    "depends_on": [],
                }
            ]

    def _parse_steps(self, raw_steps: list[dict]) -> list[PlanStep]:
        """Convert raw LLM output dicts into PlanStep objects."""
        steps = []
        for raw in raw_steps:
            steps.append(
                PlanStep(
                    id=raw["id"],
                    specialist=raw["specialist"],
                    task_description=raw["task_description"],
                    depends_on=raw.get("depends_on", []),
                )
            )
        return steps

    def _topological_waves(self, steps: list[PlanStep]) -> list[list[str]]:
        """
        Group steps into parallel execution waves using topological sort
        (Kahn's algorithm). Steps within the same wave have no inter-dependencies
        and can run concurrently.
        """
        step_ids = {s.id for s in steps}
        in_degree: dict[str, int] = {s.id: 0 for s in steps}
        dependents: dict[str, list[str]] = defaultdict(list)

        for step in steps:
            for dep in step.depends_on:
                if dep in step_ids:
                    in_degree[step.id] += 1
                    dependents[dep].append(step.id)

        waves: list[list[str]] = []
        ready = deque(sid for sid, deg in in_degree.items() if deg == 0)

        while ready:
            wave = []
            next_ready: deque[str] = deque()
            while ready:
                sid = ready.popleft()
                wave.append(sid)
                for dep_id in dependents[sid]:
                    in_degree[dep_id] -= 1
                    if in_degree[dep_id] == 0:
                        next_ready.append(dep_id)
            waves.append(wave)
            ready = next_ready

        return waves
