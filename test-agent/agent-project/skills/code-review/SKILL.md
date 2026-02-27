---
name: Code Review
description: Reviews code for bugs, style issues, and security vulnerabilities using deterministic checks and LLM analysis.
category: document-asset-creation
triggers:
  - review code
  - code review
  - check this code
  - find bugs
---

# Code Review Skill

## Purpose
Provide thorough code reviews that combine deterministic pattern-checking
scripts with LLM-powered analysis.

## Workflow

1. **Read the code** — Use the `read_file` tool to load the file(s) to review.
2. **Run deterministic checks** — Execute `scripts/check_patterns.py` against
   the code (Level 3 — load only at this step). This catches common issues
   mechanistically before LLM analysis.
3. **LLM analysis** — Review the code for:
   - Logic errors and edge cases
   - Security vulnerabilities (injection, auth issues, data exposure)
   - Performance concerns
   - Readability and maintainability
4. **Produce report** — Combine script output and LLM findings into a
   structured review.

## Output Format

### Summary
One paragraph overall assessment.

### Issues Found
For each issue:
- **Severity**: Critical / Warning / Suggestion
- **Location**: file:line_number
- **Description**: What's wrong and why
- **Fix**: Recommended change

### Checklist
- [ ] No hardcoded secrets
- [ ] Input validation on external data
- [ ] Error handling is appropriate
- [ ] No obvious performance bottlenecks
