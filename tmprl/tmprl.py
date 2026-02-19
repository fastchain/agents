"""
Temporal + MCP (Model Context Protocol) Integration Examples in Python.

Three approaches:
1. Official Temporal SDK with StatelessMCPServerProvider (OpenAI Agents)
2. Official Temporal SDK with StatefulMCPServerProvider (OpenAI Agents)
3. DIY: Manual MCP client calls inside Temporal activities

Requirements:
    pip install temporalio openai-agents mcp
"""

import asyncio
import dataclasses
from datetime import timedelta

# =============================================================================
# APPROACH 1: Official Temporal SDK - Stateless MCP Server (OpenAI Agents)
# =============================================================================


# --- worker.py ---

from agents.mcp import MCPServerStdio
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.contrib.openai_agents import (
    ModelActivityParameters,
    OpenAIAgentsPlugin,
    StatelessMCPServerProvider,
)

# Wrap any MCPServer in a StatelessMCPServerProvider.
# Each call_tool / list_tools becomes a Temporal activity (durable, retryable).
filesystem_server = StatelessMCPServerProvider(
    name="FileSystemServer",
    server_factory=lambda: MCPServerStdio(
        name="filesystem",
        params={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/data"],
        },
    ),
)


async def run_worker():
    client = await Client.connect(
        "localhost:7233",
        plugins=[
            OpenAIAgentsPlugin(
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(seconds=60),
                ),
                mcp_server_providers=[filesystem_server],
            ),
        ],
    )

    worker = Worker(
        client,
        task_queue="mcp-agent-queue",
        workflows=[FileAssistantWorkflow],
        activities=filesystem_server._get_activities(),
    )
    await worker.run()


# --- workflows.py ---

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from agents import Agent, Runner
    import temporalio.contrib.openai_agents as openai_agents


@workflow.defn
class FileAssistantWorkflow:
    @workflow.run
    async def run(self, query: str) -> str:
        # Get the MCP server reference (calls become activities under the hood)
        server = openai_agents.workflow.stateless_mcp_server("FileSystemServer")

        agent = Agent(
            name="File Assistant",
            instructions="Use filesystem tools to answer questions about files.",
            mcp_servers=[server],
        )

        result = await Runner.run(agent, input=query)
        return result.final_output


# --- client.py ---

async def start_workflow():
    client = await Client.connect("localhost:7233")

    result = await client.execute_workflow(
        "FileAssistantWorkflow",
        "List all files in the /tmp/data directory",
        id="mcp-file-assistant-1",
        task_queue="mcp-agent-queue",
    )
    print(result)


# =============================================================================
# APPROACH 2: DIY - MCP Client Inside Temporal Activities
# =============================================================================

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from temporalio import activity


@dataclasses.dataclass
class MCPToolRequest:
    server_command: str
    server_args: list[str]
    tool_name: str
    tool_arguments: dict


@activity.defn
async def call_mcp_tool(request: MCPToolRequest) -> dict:
    """Temporal activity that calls an MCP tool."""
    server_params = StdioServerParameters(
        command=request.server_command,
        args=request.server_args,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                request.tool_name,
                arguments=request.tool_arguments,
            )

            return {
                "content": [
                    {"type": c.type, "text": getattr(c, "text", "")}
                    for c in result.content
                ],
                "isError": result.isError,
            }


@activity.defn
async def list_mcp_tools(server_command: str, server_args: list[str]) -> list[dict]:
    """Temporal activity that lists tools from an MCP server."""
    server_params = StdioServerParameters(
        command=server_command,
        args=server_args,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema,
                }
                for tool in tools_result.tools
            ]


@workflow.defn
class MCPOrchestratorWorkflow:
    @workflow.run
    async def run(self, task: str) -> dict:
        server_cmd = "npx"
        server_args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/data"]

        # Step 1: Discover available tools (durable activity)
        tools = await workflow.execute_activity(
            list_mcp_tools,
            args=[server_cmd, server_args],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Step 2: Call a specific tool (durable activity)
        result = await workflow.execute_activity(
            call_mcp_tool,
            MCPToolRequest(
                server_command=server_cmd,
                server_args=server_args,
                tool_name="list_directory",
                tool_arguments={"path": "/tmp/data"},
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )

        return {"available_tools": tools, "result": result}


async def run_diy_worker():
    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue="mcp-queue",
        workflows=[MCPOrchestratorWorkflow],
        activities=[call_mcp_tool, list_mcp_tools],
    )
    await worker.run()


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    import sys

    usage = """
Usage:
    python tmprl.py worker       # Run the OpenAI Agents + MCP worker
    python tmprl.py diy-worker   # Run the DIY MCP worker
    python tmprl.py client       # Start the FileAssistantWorkflow
    """

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    match sys.argv[1]:
        case "worker":
            asyncio.run(run_worker())
        case "diy-worker":
            asyncio.run(run_diy_worker())
        case "client":
            asyncio.run(start_workflow())
        case _:
            print(usage)
            sys.exit(1)
