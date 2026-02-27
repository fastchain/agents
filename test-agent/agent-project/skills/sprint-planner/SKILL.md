---
name: Sprint Planner
description: Creates sprint plans from backlogs using INVEST criteria, optionally pulling issues from GitHub/Jira via MCP.
category: mcp-enhancement
triggers:
  - plan sprint
  - sprint planning
  - create sprint
  - prioritize backlog
---

# Sprint Planner Skill

## Purpose
Help teams plan sprints by pulling backlog items (via MCP or manual input),
evaluating them against INVEST criteria, and producing a prioritized sprint
plan.

## Workflow

1. **Gather backlog** — If MCP is enabled, use the GitHub or Jira MCP server
   to fetch open issues/tickets. Otherwise, ask the user to provide backlog
   items.
2. **Evaluate with INVEST** — Score each item against INVEST criteria.
   Load `references/invest-criteria.md` for the rubric (Level 3).
3. **Estimate effort** — Assign story points using a Fibonacci scale
   (1, 2, 3, 5, 8, 13).
4. **Prioritize** — Rank items by value/effort ratio. Consider dependencies.
5. **Assemble sprint** — Fill the sprint capacity (default: sum of story
   points that fits the team velocity) with the top-ranked items.

## Output Format

### Sprint Goal
One sentence describing the sprint's objective.

### Selected Items
| # | Title | Points | INVEST Score | Priority |
|---|-------|--------|-------------|----------|
| 1 | ...   | ...    | ...         | ...      |

### Deferred Items
Items that didn't fit, with a brief reason.

### Risks & Dependencies
Bullet list of cross-item dependencies or external blockers.
