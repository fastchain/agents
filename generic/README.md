# nmap MCP Server

An MCP (Model Context Protocol) server that gives LLMs the ability to run nmap network scans, backed by Temporal workflows for reliability.

## Architecture

```
┌─────────────┐     MCP (Streamable HTTP / stdio)     ┌──────────────┐
│  LLM Client │ ◄──────────────────────────────────► │  MCP Server  │
│  (Claude,   │     Tools: start_nmap_scan,           │  :8000       │
│  OpenWebUI) │     check_scan_status, ...            └──────┬───────┘
└─────────────┘                                              │
                                                             │ gRPC
                                                    ┌────────▼────────┐
                                                    │    Temporal     │
                                                    │    :7233        │
                                                    │    UI :8080     │
                                                    └────────┬────────┘
                                                             │
                                                    ┌────────▼────────┐
                                                    │     Worker      │
                                                    │  (nmap inside)  │
                                                    │  NET_RAW cap    │
                                                    └─────────────────┘

Workflow steps:  validate_input ──► run_nmap ──► parse_xml ──► result
```

## Example LLM Conversation

```
User: Scan scanme.nmap.org for open ports with version detection

LLM:  [calls start_nmap_scan(target="scanme.nmap.org", nmap_args="-sT -sV --top-ports 100")]
      → scan_id: scan-a1b2c3d4e5f6

LLM:  Scan started! I'll check on it in a moment.

      [calls check_scan_status(scan_id="scan-a1b2c3d4e5f6")]
      → status: RUNNING

LLM:  Still running — version detection takes a bit. Let me check again...

      [calls check_scan_status(scan_id="scan-a1b2c3d4e5f6")]
      → status: COMPLETED

      [calls get_scan_results(scan_id="scan-a1b2c3d4e5f6")]
      → { hosts: [...], summary: "..." }

LLM:  Here are the results for scanme.nmap.org:
      - 22/tcp: OpenSSH 6.6.1p1
      - 80/tcp: Apache httpd 2.4.7
      ...
```

## Quick Start

### Docker (recommended)

```bash
docker compose up --build -d
```

This starts all 4 services:
- **Temporal** on `:7233` (gRPC) and `:8233` (HTTP)
- **Temporal UI** on `:8080`
- **Worker** (executes nmap scans)
- **MCP Server** on `:8000` (Streamable HTTP)

### Local Development

```bash
# 1. Start Temporal (requires a running instance)
temporal server start-dev

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the worker (in one terminal)
python worker.py

# 4. Start the MCP server (in another terminal)
python mcp_server.py
```

For local dev, update the Temporal host in `worker.py` and `mcp_server.py` from `temporal:7233` to `localhost:7233`.

## OpenWebUI Integration

### Method 1: Native Streamable HTTP (OpenWebUI v0.6.31+)

1. Start the stack: `docker compose up --build -d`
2. In OpenWebUI, go to **Settings → Tools → Add MCP Server**
3. Set URL: `http://localhost:8000/mcp`
4. Save — the 5 tools appear automatically

### Method 2: mcpo Proxy

For older OpenWebUI versions that only support OpenAPI-based tool servers:

```bash
# Install mcpo
pip install mcpo

# Run the proxy (bridges OpenAPI ↔ MCP stdio)
mcpo --port 8001 -- python mcp_server.py --stdio
```

Then in OpenWebUI: **Settings → Tools → Add Tool Server** → `http://localhost:8001`

### Method 3: stdio with mcpo Config File

Create `mcpo_config.json`:

```json
{
  "mcpServers": {
    "nmap-scanner": {
      "command": "python",
      "args": ["mcp_server.py", "--stdio"]
    }
  }
}
```

```bash
mcpo --port 8001 --config mcpo_config.json
```

## Tools Reference

| Tool | Type | Description |
|------|------|-------------|
| `start_nmap_scan(target, nmap_args, label)` | Non-blocking | Starts a scan workflow, returns `scan_id` immediately |
| `check_scan_status(scan_id)` | Non-blocking | Returns workflow status: RUNNING, COMPLETED, FAILED |
| `get_scan_results(scan_id)` | Non-blocking | Fetches parsed results (only works when COMPLETED) |
| `run_quick_scan(target, nmap_args)` | Blocking | Starts and waits for result — use for fast scans only |
| `list_recent_scans()` | Non-blocking | Lists all scans from this session with status |

## Security

### What's Blocked

Input validation (`validate_scan_input` activity) prevents command injection:

- **Shell metacharacters**: `; & | \` $ ( ) { } < >` and newlines
- **Dangerous nmap flags**: `--script-args`, `-iL`, `-oN`, `-oX`, `-oG`, `-oA`, `--datadir`
- **Malformed arguments**: anything that fails `shlex.split()` parsing

### Root / NET_RAW Requirements

Some nmap features require elevated privileges:

| Feature | Requires |
|---------|----------|
| `-sS` (SYN scan) | `NET_RAW` capability or root |
| `-O` (OS detection) | `NET_RAW` capability or root |
| `-sU` (UDP scan) | `NET_RAW` capability or root |
| `-sT` (TCP connect) | No special privileges |
| `-sV` (version detect) | No special privileges |

The Docker worker container has `cap_add: NET_RAW` to support privileged scans.

## Why Temporal?

### What breaks without it

- **Long scans die silently**: A 30-minute full-port scan over HTTP will time out and you lose the results
- **No retry on failure**: If nmap crashes mid-scan, you start over manually
- **No visibility**: You can't check if a scan is still running or why it failed
- **No concurrent scans**: Hard to manage multiple scans at once

### What you get with it

- **Durable execution**: Scans survive server restarts, network blips, and container restarts
- **Automatic retries**: Failed scans retry up to 3 times with backoff
- **Heartbeat monitoring**: Temporal detects stuck activities (via 10-second heartbeats) and reschedules them
- **Observability**: Full workflow history in the Temporal UI at `:8080` — see every step, input, output, and failure
- **Async-native**: Start a scan now, check results later — the workflow runs independently
- **Scalable**: Add more worker containers to run scans in parallel across machines
# agents
