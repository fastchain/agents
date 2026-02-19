from fastmcp import FastMCP

# Create an MCP server named "MathAgent"
mcp = FastMCP("MathAgent")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers"""
    return a * b

if __name__ == "__main__":
    # Run the server over stdio (standard input/output)
    mcp.run(transport='stdio')
