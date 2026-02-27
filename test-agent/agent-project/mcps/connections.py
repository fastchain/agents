"""MCP server connections — Slack, GitHub, etc.

Each connection is defined declaratively. The manager handles startup,
health checks, and graceful shutdown of server processes.
"""

import os
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: str  # e.g. "npx -y @modelcontextprotocol/server-github"
    env: dict[str, str] = field(default_factory=dict)


# Define your MCP servers here.  Disabled by default — set
# config.settings.enable_mcps = True and provide the required env vars.
MCP_SERVERS: list[MCPServerConfig] = [
    MCPServerConfig(
        name="github",
        command="npx -y @modelcontextprotocol/server-github",
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ.get("GITHUB_TOKEN", "")},
    ),
    MCPServerConfig(
        name="slack",
        command="npx -y @anthropic/mcp-server-slack",
        env={"SLACK_BOT_TOKEN": os.environ.get("SLACK_BOT_TOKEN", "")},
    ),
]


def get_mcp_tools() -> list[BaseTool]:
    """Start configured MCP servers and return their tools.

    In a full implementation this would use langchain_mcp_adapters to
    spin up each server process and convert its capabilities into
    LangChain-compatible tools. Returns an empty list as a placeholder.
    """
    # Placeholder — integrate langchain-mcp-adapters for real MCP support:
    #
    #   from langchain_mcp_adapters.client import MultiServerMCPClient
    #   async with MultiServerMCPClient(servers) as client:
    #       return client.get_tools()
    #
    return []
