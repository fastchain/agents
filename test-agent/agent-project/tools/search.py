"""Web search tool — wraps a search API to retrieve current information."""

from langchain_core.tools import tool


@tool
def web_search(query: str) -> str:
    """Search the web for current information on a topic.

    Use this tool when the user asks about recent events, needs up-to-date
    facts, or when your training data may be outdated. Returns a summary
    of the top search results.

    Args:
        query: The search query string. Be specific for better results.
    """
    # In production, replace with a real search API (Tavily, SerpAPI, etc.)
    return (
        f'[Search results for "{query}"]\n'
        "1. Result placeholder — integrate a search provider such as "
        "Tavily (pip install tavily-python) for real results.\n"
        "2. See https://python.langchain.com/docs/integrations/tools/ "
        "for supported search providers."
    )
