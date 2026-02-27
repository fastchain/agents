---
name: Research Assistant
description: Conducts multi-step web research, synthesizes findings into structured reports.
category: workflow-automation
triggers:
  - research
  - summarize topic
  - literature review
  - what are the latest
---

# Research Assistant Skill

## Purpose
Automate the research workflow: plan queries, search iteratively, synthesize
findings, and produce a structured report.

## Workflow

1. **Clarify scope** — Confirm the topic, depth, and desired output format
   with the user.
2. **Plan queries** — Break the topic into 3-5 specific search queries that
   cover different facets.
3. **Search iteratively** — Use the `web_search` tool for each query. Assess
   whether results are sufficient or if follow-up queries are needed.
4. **Synthesize** — Combine findings into a coherent report. Follow the
   synthesis guide in `references/synthesis-guide.md` for structure
   (load it only at this step — Level 3).
5. **Cite sources** — Every factual claim must reference a source URL.

## Output Format
- Title
- Executive Summary (3-5 sentences)
- Key Findings (bulleted)
- Detailed Analysis (sections per sub-topic)
- Sources (numbered list of URLs)

## Quality Checklist
- [ ] All claims are sourced
- [ ] No contradictions between sections
- [ ] Executive summary is self-contained
