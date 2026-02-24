# IRC Multi-Agent Bus Framework

Multi-agent orchestration over IRC channels. Queues are IRC channels.

IRC server settings (default): `localhost:6667`.

## Project Layout

```text
.
├── agents/
│   ├── orchestrator/
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── skills/
│   │   │   └── project_decomposition.md
│   │   ├── tools/
│   │   │   └── task_planner.py
│   │   └── mcp/
│   │       └── bus_dashboard_server.py
│   └── specialist/
│       ├── agent.py
│       ├── config.py
│       ├── skills/
│       │   └── code_review.md
│       ├── tools/
│       │   └── code_executor.py
│       └── mcp/
│           └── knowledge_base_server.py
├── core.py
├── bus.py
├── example_multi_agent.py
├── bus_inspector.py
├── run_orchestrator.py
└── manuals/
```

## Requirements

- Python 3.10+
- `litellm`
- `rich`
- IRC server reachable at `localhost:6667`

```bash
pip install litellm rich
```

Set provider key (example OpenAI):

```bash
export OPENAI_API_KEY=...
```

## Bus Model

- Each task queue maps to an IRC channel: `queue_name -> #q_queue_name`
- Bus control/checkpoints/messages use internal channels
- Agents must connect to the same IRC server to communicate

## Run Manual (Separate Terminals)

### 1. Start specialist (Terminal A)

```bash
python -m agents.specialist.agent \
  --irc-host localhost \
  --irc-port 6667 \
  --queue demo_specialist \
  --manual manuals/demo_system.md \
  --agent-id demo-specialist-1
```

### 2. Start orchestrator (Terminal B)

```bash
python run_orchestrator.py
```

`run_orchestrator.py` is configured for `localhost:6667`.

### 3. Inspect bus (Terminal C)

```bash
python bus_inspector.py --irc-host localhost --irc-port 6667 status
python bus_inspector.py --irc-host localhost --irc-port 6667 tasks
python bus_inspector.py --irc-host localhost --irc-port 6667 checkpoints
```

## One-Process Demo

```bash
python example_multi_agent.py
```

## MCP Servers

### Orchestrator bus dashboard MCP

```bash
BUS_IRC_HOST=localhost BUS_IRC_PORT=6667 python -m agents.orchestrator.mcp.bus_dashboard_server
```

### Specialist knowledge-base MCP

```bash
MANUALS_DIR=./manuals python -m agents.specialist.mcp.knowledge_base_server
```

## Notes

- The old `--db` flag is kept only for backward compatibility and is ignored by the IRC bus.
- Queue names become IRC channel names; only letters, digits, `_`, and `-` are preserved.
