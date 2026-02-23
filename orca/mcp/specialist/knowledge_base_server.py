"""
mcp/specialist/knowledge_base_server.py — MCP server for manual search & retrieval

An MCP server that indexes markdown manual files and exposes search/retrieval
tools. Specialists (or any LLM client) can search across all loaded manuals
by keyword, retrieve specific sections, or browse manual outlines.

Run:
    MANUALS_DIR=manuals/ python mcp/specialist/knowledge_base_server.py

Tools exposed:
    - search_manual    → Keyword search across all manual sections
    - get_section      → Retrieve a specific section by manual name + heading
    - list_manuals     → List all indexed manuals
    - get_manual_outline → Get the heading structure of a manual
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mcp.server.fastmcp import FastMCP


# ── Manual indexing ────────────────────────────────────────────────────────


@dataclass
class Section:
    """A single section of a manual, identified by its heading."""

    heading: str           # e.g. "## Installation"
    level: int             # heading level (1-6)
    content: str           # full text of the section (including heading)
    manual_name: str       # which manual this belongs to


@dataclass
class ManualIndex:
    """Parses markdown files into sections and provides search capabilities."""

    manuals_dir: str
    sections: list[Section] = field(default_factory=list)
    manual_names: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._load_all()

    def _load_all(self):
        """Scan manuals directory and index all .md files."""
        manuals_path = Path(self.manuals_dir)
        if not manuals_path.is_dir():
            return

        for md_file in sorted(manuals_path.glob("**/*.md")):
            manual_name = md_file.stem
            self.manual_names.append(manual_name)
            text = md_file.read_text(encoding="utf-8")
            self._parse_sections(text, manual_name)

    def _parse_sections(self, text: str, manual_name: str):
        """Split a markdown document into sections by heading."""
        heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        matches = list(heading_pattern.finditer(text))

        if not matches:
            # No headings — treat the whole document as one section
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
        """
        Keyword search across all sections. Scores sections by keyword
        frequency in both heading and content.

        Args:
            query: Space-separated keywords to search for.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with manual_name, heading, score, and content_preview.
        """
        keywords = [kw.lower() for kw in query.split() if kw.strip()]
        if not keywords:
            return []

        scored = []
        for section in self.sections:
            score = 0
            heading_lower = section.heading.lower()
            content_lower = section.content.lower()

            for kw in keywords:
                # Heading matches are worth more
                score += heading_lower.count(kw) * 3
                score += content_lower.count(kw)

            if score > 0:
                # Create a preview: first 200 chars of content
                preview = section.content[:200].replace("\n", " ")
                if len(section.content) > 200:
                    preview += "..."

                scored.append({
                    "manual_name": section.manual_name,
                    "heading": section.heading,
                    "level": section.level,
                    "score": score,
                    "content_preview": preview,
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:max_results]

    def get_section(self, manual_name: str, heading: str) -> Section | None:
        """Get a specific section by manual name and heading."""
        for section in self.sections:
            if section.manual_name == manual_name and section.heading == heading:
                return section
        return None

    def get_outline(self, manual_name: str) -> list[dict]:
        """Get the heading structure of a specific manual."""
        outline = []
        for section in self.sections:
            if section.manual_name == manual_name:
                outline.append({
                    "heading": section.heading,
                    "level": section.level,
                })
        return outline


# ── Server setup ───────────────────────────────────────────────────────────

MANUALS_DIR = os.environ.get("MANUALS_DIR", "manuals")

mcp = FastMCP("Knowledge Base")
index = ManualIndex(MANUALS_DIR)


# ── Tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def search_manual(query: str, max_results: int = 10) -> list[dict]:
    """Search across all manuals by keyword.

    Args:
        query: Space-separated keywords to search for.
        max_results: Maximum results to return (default 10).

    Returns a list of matching sections with scores and previews, ranked
    by relevance. Heading matches are weighted 3x higher than body matches.
    """
    return index.search(query, max_results=max_results)


@mcp.tool()
def get_section(manual_name: str, heading: str) -> dict | str:
    """Retrieve the full content of a specific manual section.

    Args:
        manual_name: Name of the manual (filename without .md extension).
        heading: The section heading text (e.g. "Installation").

    Returns the full section content or an error message if not found.
    """
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
    """List all indexed manuals by name.

    Returns a list of manual names (filename stems). Use get_manual_outline
    to see the sections within a specific manual.
    """
    return index.manual_names


@mcp.tool()
def get_manual_outline(manual_name: str) -> list[dict] | str:
    """Get the heading structure of a specific manual.

    Args:
        manual_name: Name of the manual (filename without .md extension).

    Returns a list of headings with their levels, or an error if the manual
    is not found.
    """
    outline = index.get_outline(manual_name)
    if not outline:
        return f"Manual '{manual_name}' not found or has no headings"
    return outline


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
