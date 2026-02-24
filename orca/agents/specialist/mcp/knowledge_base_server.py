"""MCP server for manual search and retrieval for specialist agents."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.fastmcp import FastMCP


@dataclass
class Section:
    heading: str
    level: int
    content: str
    manual_name: str


@dataclass
class ManualIndex:
    manuals_dir: str
    sections: list[Section] = field(default_factory=list)
    manual_names: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._load_all()

    def _load_all(self):
        manuals_path = Path(self.manuals_dir)
        if not manuals_path.is_dir():
            return

        for md_file in sorted(manuals_path.glob("**/*.md")):
            manual_name = md_file.stem
            self.manual_names.append(manual_name)
            text = md_file.read_text(encoding="utf-8")
            self._parse_sections(text, manual_name)

    def _parse_sections(self, text: str, manual_name: str):
        heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        matches = list(heading_pattern.finditer(text))

        if not matches:
            self.sections.append(
                Section(
                    heading=manual_name,
                    level=1,
                    content=text.strip(),
                    manual_name=manual_name,
                )
            )
            return

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            level = len(match.group(1))
            heading = match.group(2).strip()
            content = text[start:end].strip()

            self.sections.append(
                Section(
                    heading=heading,
                    level=level,
                    content=content,
                    manual_name=manual_name,
                )
            )

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        keywords = [kw.lower() for kw in query.split() if kw.strip()]
        if not keywords:
            return []

        scored = []
        for section in self.sections:
            score = 0
            heading_lower = section.heading.lower()
            content_lower = section.content.lower()

            for kw in keywords:
                score += heading_lower.count(kw) * 3
                score += content_lower.count(kw)

            if score > 0:
                preview = section.content[:200].replace("\n", " ")
                if len(section.content) > 200:
                    preview += "..."

                scored.append(
                    {
                        "manual_name": section.manual_name,
                        "heading": section.heading,
                        "level": section.level,
                        "score": score,
                        "content_preview": preview,
                    }
                )

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:max_results]

    def get_section(self, manual_name: str, heading: str) -> Section | None:
        for section in self.sections:
            if section.manual_name == manual_name and section.heading == heading:
                return section
        return None

    def get_outline(self, manual_name: str) -> list[dict]:
        outline = []
        for section in self.sections:
            if section.manual_name == manual_name:
                outline.append({"heading": section.heading, "level": section.level})
        return outline


MANUALS_DIR = os.environ.get("MANUALS_DIR", "manuals")

mcp = FastMCP("Knowledge Base")
index = ManualIndex(MANUALS_DIR)


@mcp.tool()
def search_manual(query: str, max_results: int = 10) -> list[dict]:
    return index.search(query, max_results=max_results)


@mcp.tool()
def get_section(manual_name: str, heading: str) -> dict | str:
    section = index.get_section(manual_name, heading)
    if section is None:
        return f"Section '{heading}' not found in manual '{manual_name}'"
    return {
        "manual_name": section.manual_name,
        "heading": section.heading,
        "level": section.level,
        "content": section.content,
    }


@mcp.tool()
def list_manuals() -> list[str]:
    return index.manual_names


@mcp.tool()
def get_manual_outline(manual_name: str) -> list[dict] | str:
    outline = index.get_outline(manual_name)
    if not outline:
        return f"Manual '{manual_name}' not found or has no headings"
    return outline


def main():
    mcp.run()


if __name__ == "__main__":
    main()
