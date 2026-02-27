"""MCP client manager — discovers and connects to configured MCP servers.

MCP (Model Context Protocol) servers expose external capabilities like
Slack, GitHub, or Jira. They have different lifecycles, auth patterns,
and error modes compared to local Python tools.
"""

from .connections import get_mcp_tools


__all__ = ["get_mcp_tools"]
