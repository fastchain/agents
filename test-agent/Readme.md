# Claude Agent — Modular LangChain Project

A clean, modular agent project following [Anthropic's "Building Effective Agents"](https://www.anthropic.com/research/building-effective-agents) guidelines and their context-engineering best practices.

## Architecture Principles (from Anthropic)

1. **Start simple** — Don't add complexity unless it demonstrably improves outcomes.
2. **Progressive context loading** — Only load tools/skills the agent actually needs for a given task (reduces token usage).
3. **Well-documented tools** — Claude relies on tool descriptions to decide when/how to use them. Be specific.
4. **Skills pattern** — Domain knowledge lives in `SKILL.md` files that the agent reads on-demand, not all at once.
5. **Separation of concerns** — Tools, MCP servers, skills, and prompts each live in their own folder.

## Project Structure

```
agent-project/
├── agent.py                  # Main entrypoint — creates and runs the agent
├── config/
│   ├── __init__.py
│   └── settings.py           # Model params, API keys, feature flags
├── prompts/
│   ├── __init__.py
│   └── system.py             # System prompt builder (uses XML tags per Anthropic guidelines)
├── tools/
│   ├── __init__.py           # Auto-discovers & exports all tools
│   ├── search.py             # Web search tool
│   ├── database.py           # Database query tool
│   └── file_ops.py           # File read/write tools
├── mcps/
│   ├── __init__.py           # MCP client manager
│   └── connections.py        # MCP server connections (Slack, GitHub, etc.)
├── skills/                       # Agent Skills (Anthropic pattern)
│   ├── research-assistant/       # Each skill = kebab-case folder
│   │   ├── SKILL.md              # Required — YAML frontmatter + instructions
│   │   └── references/           # Level 3: loaded on-demand
│   │       └── synthesis-guide.md
│   ├── code-review/
│   │   ├── SKILL.md
│   │   └── scripts/              # Deterministic checks supplement LLM review
│   │       └── check_patterns.py
│   └── sprint-planner/           # MCP Enhancement skill (Category 3)
│       ├── SKILL.md
│       └── references/
│           └── invest-criteria.md
├── requirements.txt
└── README.md
```

## Quick Start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python agent.py "What are the latest trends in AI safety?"
```

## Key Design Decisions

### Why separate `tools/` and `mcps/`?

**Tools** are Python functions you define and control (search, DB queries, calculations).
**MCPs** are external servers that expose capabilities via the Model Context Protocol
(Slack, GitHub, Jira). They have different lifecycles, auth patterns, and error modes.

### Why `skills/` with SKILL.md files?

Following [Anthropic's Complete Guide to Building Skills](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf):

Skills use **3-level progressive disclosure** to minimize token usage:
- **Level 1** (YAML frontmatter): Name + description. Always in the system prompt
  so the agent knows when to activate a skill. ~50 tokens per skill.
- **Level 2** (SKILL.md body): Full instructions. Loaded only when the agent
  decides the skill is relevant to the current task.
- **Level 3** (references/, scripts/): Detailed docs and executable code.
  Loaded on-demand within a skill, only when specific steps need them.

Each skill folder follows a standard structure:
```
skill-name/           # kebab-case required
├── SKILL.md          # Required (exact case). YAML frontmatter + Markdown instructions
├── scripts/          # Optional: executable code (deterministic > language instructions)
├── references/       # Optional: detailed docs loaded on-demand
└── assets/           # Optional: templates, fonts, icons
```

The three included skills demonstrate the three categories from the guide:
- **research-assistant** — Workflow Automation (Category 2)
- **code-review** — Document & Asset Creation (Category 1) with bundled scripts
- **sprint-planner** — MCP Enhancement (Category 3)

### Why `prompts/` is separate?

System prompts are the #1 lever for agent quality. Keeping them in dedicated files makes
them easy to version, A/B test, and review. The prompt builder uses XML tags as
recommended by Anthropic for structured instructions.