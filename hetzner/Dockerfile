# Use a lightweight Python image
FROM python:3.11-slim

WORKDIR /app

# Install FastMCP with SSE support, Uvicorn (web server), and the Hetzner Cloud client
RUN pip install "fastmcp[sse]" uvicorn hcloud

# Copy the server script into the container
COPY server.py .

# Expose the port we defined in the script
EXPOSE 8000

# Run the script
CMD ["python","-u", "server.py"]
