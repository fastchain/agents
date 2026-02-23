# Durable Multi-Agent Orchestration Framework

A crash-resilient multi-agent system built on a SQLite-backed message bus. Agents can be killed and restarted at any time — all state is persisted, no work is lost.

## Architecture

```
                       +-----------------+
                       |   Orchestrator  |  Breaks down tasks, delegates to specialists
                       +-------+---------+
                               |
                        publish_task / wait_for_result
                               |
                       +-------v---------+
                       |   DurableBus    |  SQLite (WAL mode) — the single source of truth
                       | (core.py)       |  Tasks, checkpoints, messages
                       +---+-------+-----+
                           |       |
                    claim_task   claim_task
                           |       |
                   +-------v--+ +--v--------+
                   |Specialist| |Specialist |  Each has a domain manual in its system prompt
                   +----------+ +-----------+
```

**Components:**

| File / Directory | Description |
|---|---|
| `core.py` | `DurableBus` — SQLite-backed message bus with tasks, checkpoints, and pub/sub messages |
| `orchestrator.py` | `ResumableOrchestrator` — LLM-powered orchestrator that delegates work to specialists |
| `specialist.py` | `SpecialistAgent` — Worker agent that processes tasks using a domain-specific manual |
| `bus_inspector.py` | CLI tool to inspect bus state (tasks, checkpoints, messages) |
| `example_multi_agent.py` | End-to-end demo showing orchestrator + specialist working together |
| `tools/` | Reusable tool classes — `TaskPlanner` (orchestrator), `CodeExecutor` (specialist) |
| `mcp/` | MCP servers — `bus_dashboard_server` (orchestrator), `knowledge_base_server` (specialist) |
| `skills/` | Markdown skill/manual files for agent behaviors |

## Requirements

- Python 3.10+
- [LiteLLM](https://github.com/BerriAI/litellm) (for LLM calls)
- [Rich](https://github.com/Textualize/rich) (for terminal output)

```bash
pip install litellm rich
```

You also need a `config.py` in the project root that exports:

```python
DEFAULT_MODEL = "gpt-4"          # or any LiteLLM-supported model string
SKILLS_DIR = "skills/"           # directory for skill definitions
```

Set the appropriate API key environment variable for your chosen model (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

## Quick Start

### Run the demo

The example script spins up an orchestrator and a specialist in a single process:

```bash
python example_multi_agent.py
```

This will:
1. Create a demo specialist manual in `manuals/demo_system.md`
2. Start a specialist listening on the `demo_specialist` queue
3. Run the orchestrator, which delegates tasks and collects results
4. Save all state to `demo_bus.db`

Run it again — the orchestrator sees the session is already done and skips it.

### Simulate crash and recovery

```bash
# Run and simulate a crash after first delegation
python example_multi_agent.py --crash

# Resume the same session — orchestrator picks up where it left off
python example_multi_agent.py --resume
```

## Usage

### 1. Create the bus

```python
from core import DurableBus

bus = DurableBus("my_agents.db")
```

### 2. Set up and run a specialist

Create a markdown manual for your specialist's domain, then start it:

```bash
python specialist.py --queue db_specialist --manual manuals/postgres.md
```

Options:
- `--queue` (required) — Bus queue to listen on
- `--manual` (required) — Path to the domain manual `.md` file
- `--model` — LiteLLM model string (default from `config.py`)
- `--db` — SQLite database path (default: `agent_bus.db`)
- `--agent-id` — Instance ID (auto-generated if not set)

Or programmatically:

```python
from core import DurableBus
from specialist import SpecialistAgent

bus = DurableBus("my_agents.db")
specialist = SpecialistAgent(
    bus=bus,
    queue="db_specialist",
    manual_path="manuals/postgres.md",
)
specialist.run_forever()  # blocks, polls for tasks
```

Multiple specialists can run on the same queue — they compete atomically for tasks.

### 3. Set up and run the orchestrator

```python
from core import DurableBus
from orchestrator import ResumableOrchestrator

bus = DurableBus("my_agents.db")
orch = ResumableOrchestrator(bus=bus)

# Register available specialists
orch.register_specialist(
    tool_name="ask_db_specialist",
    queue="db_specialist",
    description="Handles PostgreSQL database tasks",
)

# Run a session (resumable by session_id)
result = orch.run(session_id="session-001", initial_task="Set up a connection pool")
```

If the orchestrator crashes, call `orch.run()` with the same `session_id` — it resumes from its last checkpoint.

### 4. Inspect the bus

```bash
# Show queue status overview
python bus_inspector.py status

# List tasks (optionally filter by queue or status)
python bus_inspector.py tasks
python bus_inspector.py tasks --queue db_specialist
python bus_inspector.py tasks --status failed

# Show details for a specific task (supports ID prefix matching)
python bus_inspector.py task <task_id>

# Show agent checkpoints
python bus_inspector.py checkpoints
python bus_inspector.py checkpoints --agent orchestrator
```

## Core Concepts

### Tasks

The unit of work. Created by the orchestrator, claimed by specialists.

```
pending  -->  processing  -->  done
                  |
                  +----------->  failed (after max_retries exceeded)
                  |
                  +----------->  pending (auto-requeued on agent crash)
```

- Tasks stuck in `processing` longer than 120 seconds are automatically requeued (the agent is assumed to have crashed).
- Failed tasks are retried up to `max_retries` (default: 3) before being marked as permanently failed.

### Checkpoints

Named key-value snapshots of agent progress. Agents write checkpoints periodically so they can resume after a restart.

```python
bus.save_checkpoint("orchestrator", "current_step", "waiting_for_db")
step = bus.load_checkpoint("orchestrator", "current_step")
```

### Messages

One-way pub/sub event notifications between agents.

```python
bus.publish_message("task_events", {"event": "task_done", "id": "abc"}, publisher="specialist-1")
messages = bus.get_messages("task_events", since=last_check_time)
```

## Tools

Reusable tool classes for orchestrator and specialist agents.

```
tools/
  orchestrator/
    task_planner.py           # Structured task planning & dependency ordering
  specialist/
    code_executor.py          # Sandboxed Python code execution
```

### TaskPlanner (Orchestrator)

Analyzes a high-level task and produces an `ExecutionPlan` with dependency-aware parallel waves:

```python
from core import DurableBus
from tools.orchestrator import TaskPlanner

bus = DurableBus("my_agents.db")
specialists = {
    "db_specialist": "Handles PostgreSQL database tasks",
    "api_specialist": "Builds REST API endpoints",
}

planner = TaskPlanner(bus, specialists)
plan = planner.analyze("Build a user registration system")

# View the plan as text
print(plan.to_prompt_context())

# Or as a dict (for JSON serialization)
print(plan.to_dict())
```

### CodeExecutor (Specialist)

Sandboxed Python execution with timeout, restricted builtins, and safe import whitelist:

```python
from tools.specialist import CodeExecutor

executor = CodeExecutor(timeout=5)
result = executor.run("print(sum(range(10)))")

print(result.success)           # True
print(result.stdout)            # "45\n"
print(result.to_output_string())  # formatted output for LLM responses
```

## MCP Servers

[Model Context Protocol](https://modelcontextprotocol.io/) servers that expose bus state and knowledge base search to LLM tool-calling clients.

```
mcp/
  orchestrator/
    bus_dashboard_server.py   # Bus state & control
  specialist/
    knowledge_base_server.py  # Manual search & retrieval
```

### Bus Dashboard (Orchestrator)

Exposes the DurableBus as MCP tools for inspection and control:

```bash
# Start the MCP server
BUS_DB_PATH=agent_bus.db python mcp/orchestrator/bus_dashboard_server.py
```

Available tools: `bus_status`, `get_task`, `list_tasks`, `list_checkpoints`, `submit_task`, `get_session_state`.

### Knowledge Base (Specialist)

Indexes markdown manuals and provides search/retrieval:

```bash
# Start the MCP server (indexes all .md files in the manuals directory)
MANUALS_DIR=manuals/ python mcp/specialist/knowledge_base_server.py
```

Available tools: `search_manual`, `get_section`, `list_manuals`, `get_manual_outline`.

## Skills

Markdown skill files that define agent behaviors. These can be loaded as system prompts
or passed via the `--manual` flag to specialist agents.

```
skills/
  orchestrator/
    project_decomposition.md  # Skill for decomposing projects into specialist tasks
  specialist/
    code_review.md            # Manual for a code review specialist
```

### Project Decomposition (Orchestrator)

A skill for breaking down projects into specialist-delegatable tasks. Covers component identification, specialist mapping, dependency ordering, and task description best practices.

### Code Review (Specialist)

A manual for a code review specialist agent. Load it when starting a specialist:

```bash
python specialist.py --queue code_review --manual skills/specialist/code_review.md
```

Includes a review checklist (correctness, security, performance, readability, testing), severity levels (P0–P3), structured output format, and example reviews.

## Durability Model

- **SQLite WAL mode** — concurrent reads and writes are safe
- Every write is committed before the call returns
- If a process dies mid-write, SQLite recovers automatically on next open
- Thread-safe via thread-local connections (for multi-thread within one process)
