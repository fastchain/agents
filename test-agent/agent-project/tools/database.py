"""Database query tool — runs read-only SQL against a SQLite database."""

import sqlite3

from langchain_core.tools import tool

from config import settings


@tool
def query_database(sql: str) -> str:
    """Execute a read-only SQL query against the SQLite database.

    Use this tool when the user needs to look up structured data, run
    analytics, or inspect database contents. Only SELECT statements are
    allowed for safety.

    Args:
        sql: A SELECT SQL query. INSERT/UPDATE/DELETE are rejected.
    """
    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT"):
        return "Error: Only SELECT queries are allowed for safety."

    try:
        conn = sqlite3.connect(settings.database_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(50)
        if not rows:
            return "Query returned no results."
        columns = rows[0].keys()
        lines = [" | ".join(columns)]
        lines.append(" | ".join("---" for _ in columns))
        for row in rows:
            lines.append(" | ".join(str(row[c]) for c in columns))
        conn.close()
        return "\n".join(lines)
    except Exception as e:
        return f"Database error: {e}"
