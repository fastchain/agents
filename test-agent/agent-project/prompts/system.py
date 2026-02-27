"""System prompt builder — uses XML tags per Anthropic guidelines.

Level 1 skill frontmatter is always included so the agent knows which
skills exist and when to activate them (~50 tokens per skill).
"""

import os
import re

import yaml

from config import settings


def _load_skill_summaries() -> list[dict]:
    """Parse YAML frontmatter from every skills/*/SKILL.md file (Level 1)."""
    summaries = []
    skills_dir = settings.skills_dir
    if not os.path.isdir(skills_dir):
        return summaries

    for name in sorted(os.listdir(skills_dir)):
        skill_file = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_file):
            continue
        with open(skill_file) as f:
            text = f.read()
        # Extract YAML between --- fences
        match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
        if not match:
            continue
        meta = yaml.safe_load(match.group(1))
        meta["folder"] = name
        summaries.append(meta)
    return summaries


def build_system_prompt() -> str:
    """Assemble the full system prompt with XML-tagged sections."""
    skill_summaries = _load_skill_summaries()

    skills_block = ""
    if skill_summaries:
        entries = []
        for s in skill_summaries:
            entries.append(
                f'  <skill name="{s.get("name", s["folder"])}"'
                f' folder="{s["folder"]}">'
                f'{s.get("description", "")}'
                f"</skill>"
            )
        skills_block = (
            "\n<available-skills>\n" + "\n".join(entries) + "\n</available-skills>\n"
        )

    return f"""\
<role>
You are a helpful assistant with access to tools and skills.
When the user's request matches a skill, load its SKILL.md for detailed instructions
before proceeding. Use tools to gather information and take actions.
</role>

<guidelines>
- Think step-by-step before acting.
- Use the most specific tool available for each sub-task.
- When a skill is relevant, read its SKILL.md (Level 2) first.
  Only load references/ or scripts/ (Level 3) when a specific step requires them.
- Prefer deterministic scripts over free-form generation when scripts are provided.
- Cite sources when using search results.
</guidelines>
{skills_block}"""
