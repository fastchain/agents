from fastmcp import FastMCP

# Define the server
mcp = FastMCP("MathAgent")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b

@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers"""
    return a * b

if __name__ == "__main__":
    # HOST must be 0.0.0.0 to work inside Docker
    mcp.run(transport='http', host='0.0.0.0', port=8000)
