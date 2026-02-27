#!/usr/bin/env python3
"""Deterministic pattern checker for common code issues.

Run as: python check_patterns.py <file_path>

Catches issues that don't need an LLM — hardcoded secrets, TODO comments,
overly long functions, etc.
"""

import re
import sys
from pathlib import Path

PATTERNS = [
    {
        "name": "Hardcoded secret",
        "severity": "Critical",
        "pattern": re.compile(
            r"""(password|secret|api_key|token)\s*=\s*['"][^'"]{8,}['"]""", re.IGNORECASE
        ),
    },
    {
        "name": "TODO/FIXME/HACK",
        "severity": "Suggestion",
        "pattern": re.compile(r"\b(TODO|FIXME|HACK|XXX)\b"),
    },
    {
        "name": "Bare except",
        "severity": "Warning",
        "pattern": re.compile(r"except\s*:"),
    },
    {
        "name": "Print statement (debug leftover)",
        "severity": "Suggestion",
        "pattern": re.compile(r"^\s*print\(", re.MULTILINE),
    },
]

MAX_FUNCTION_LINES = 50


def check_file(path: str) -> list[dict]:
    """Run all pattern checks against a file. Returns list of findings."""
    text = Path(path).read_text()
    lines = text.splitlines()
    findings = []

    for pat in PATTERNS:
        for i, line in enumerate(lines, 1):
            if pat["pattern"].search(line):
                findings.append(
                    {
                        "severity": pat["severity"],
                        "name": pat["name"],
                        "line": i,
                        "text": line.strip(),
                    }
                )

    # Check for long functions (Python def blocks)
    func_start = None
    for i, line in enumerate(lines, 1):
        if re.match(r"^\s*def \w+", line):
            if func_start is not None:
                length = i - func_start
                if length > MAX_FUNCTION_LINES:
                    findings.append(
                        {
                            "severity": "Suggestion",
                            "name": f"Long function ({length} lines)",
                            "line": func_start,
                            "text": lines[func_start - 1].strip(),
                        }
                    )
            func_start = i

    return findings


def main():
    if len(sys.argv) < 2:
        print("Usage: check_patterns.py <file_path>")
        sys.exit(1)

    path = sys.argv[1]
    findings = check_file(path)

    if not findings:
        print("No issues found.")
        return

    for f in findings:
        print(f"[{f['severity']}] Line {f['line']}: {f['name']}")
        print(f"  {f['text']}")
        print()


if __name__ == "__main__":
    main()
