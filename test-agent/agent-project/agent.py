"""Main entrypoint — creates and runs the agent.

Usage:
    python agent.py "What are the latest trends in AI safety?"
"""

import sys

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from config import settings
from prompts import build_system_prompt
from tools import get_tools


def build_agent():
    """Assemble the agent from config, prompt, and tools."""
    llm = ChatAnthropic(
        model=settings.model,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        api_key=settings.anthropic_api_key,
    )

    tools = get_tools()

    # Add MCP tools if enabled
    if settings.enable_mcps:
        from mcps import get_mcp_tools

        tools.extend(get_mcp_tools())

    system_prompt = build_system_prompt()

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=system_prompt,
    )
    return agent


def main():
    if len(sys.argv) < 2:
        print("Usage: python agent.py \"<your question>\"")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    agent = build_agent()

    print(f"Agent received: {query}\n")
    for chunk in agent.stream({"messages": [("user", query)]}):
        # Print agent messages as they arrive
        if "agent" in chunk:
            for msg in chunk["agent"]["messages"]:
                if hasattr(msg, "content") and isinstance(msg.content, str):
                    print(msg.content)
        elif "tools" in chunk:
            for msg in chunk["tools"]["messages"]:
                print(f"[Tool: {msg.name}] {msg.content[:200]}")


if __name__ == "__main__":
    main()
