"""Auto-discovers and exports all tools defined in this package.

Each module in tools/ should define one or more functions decorated with
@tool from langchain_core.tools. This __init__ collects them automatically
so agent.py can just do `from tools import get_tools`.
"""

import importlib
import pkgutil
from pathlib import Path

from langchain_core.tools import BaseTool

_PACKAGE_DIR = Path(__file__).parent


def get_tools() -> list[BaseTool]:
    """Import every sibling module and return all BaseTool instances found."""
    tools: list[BaseTool] = []
    for info in pkgutil.iter_modules([str(_PACKAGE_DIR)]):
        module = importlib.import_module(f".{info.name}", package=__name__)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, BaseTool):
                tools.append(attr)
    return tools
