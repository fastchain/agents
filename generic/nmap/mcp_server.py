"""MCP server exposing nmap scanning tools backed by Temporal workflows."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from temporalio.client import Client

from workflows import NmapScanInput, NmapScanWorkflow

# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

INSTRUCTIONS = """\
You are an nmap scanning assistant. You help users run and interpret network \
scans using nmap, orchestrated through Temporal workflows for reliability.

## Workflow: start → poll → get results

1. Use `start_nmap_scan()` to kick off a scan. It returns a `scan_id` immediately.
2. Use `check_scan_status(scan_id)` to poll whether it has finished.
3. Use `get_scan_results(scan_id)` to retrieve parsed results once complete.

For quick, simple scans you can use `run_quick_scan()` which blocks until done.

## IMPORTANT: Auto-check running scans

At the START of every new user message, if there are any scans that were \
previously started and might still be running, proactively call \
`check_scan_status()` for each one before doing anything else. Users expect \
you to keep them updated without being asked.

## Common nmap flags

| Flag | Purpose |
|------|---------|
| `-sV` | Version detection (probe open ports for service/version) |
| `-sT` | TCP connect scan (unprivileged, no root needed) |
| `-sS` | TCP SYN scan (stealthy, requires root/NET_RAW) |
| `-O` | OS detection (requires root/NET_RAW) |
| `-p 22,80,443` | Scan specific ports |
| `-p-` | Scan all 65535 ports |
| `--top-ports 100` | Scan top N most common ports |
| `-A` | Aggressive: OS detection + version + scripts + traceroute |
| `-T4` | Faster timing template (T0=paranoid … T5=insane) |
| `--script vuln` | Run vulnerability detection scripts |
| `-sU` | UDP scan (slow, requires root) |
| `-Pn` | Skip host discovery, treat all hosts as online |

## Timing expectations

- Quick scans (few ports, one host): seconds
- Service version scans: 30s–2min
- Full port scans (`-p-`): 5–20min
- Aggressive + vuln scripts: 10–60min
- Large subnets: potentially hours

Scans run in the background via Temporal, so they survive disconnections and \
can be retried automatically on transient failures.
"""

mcp = FastMCP(

    "nmap-scanner",
    instructions=INSTRUCTIONS,
    host="0.0.0.0",

)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_temporal_client: Client | None = None
_scan_registry: dict[str, dict[str, Any]] = {}

TASK_QUEUE = "nmap-scans"
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")
TEMPORAL_CONNECT_RETRIES = 60
TEMPORAL_RETRY_DELAY_SECONDS = 2


async def _get_client() -> Client:
    """Lazy-initialize a single Temporal client connection."""
    global _temporal_client
    if _temporal_client is None:
        last_exc: Exception | None = None
        for attempt in range(1, TEMPORAL_CONNECT_RETRIES + 1):
            try:
                _temporal_client = await Client.connect(TEMPORAL_HOST)
                break
            except Exception as exc:
                last_exc = exc
                if attempt == TEMPORAL_CONNECT_RETRIES:
                    break
                await asyncio.sleep(TEMPORAL_RETRY_DELAY_SECONDS)
        if _temporal_client is None:
            raise RuntimeError(
                f"Failed to connect to Temporal at {TEMPORAL_HOST} "
                f"after {TEMPORAL_CONNECT_RETRIES} attempts"
            ) from last_exc
    return _temporal_client


def _normalize_workflow_status(status_obj: Any) -> str:
    """Normalize Temporal status to plain values like RUNNING/COMPLETED/FAILED."""
    if status_obj is None:
        return "UNKNOWN"
    raw = getattr(status_obj, "name", str(status_obj))
    if raw.startswith("WORKFLOW_EXECUTION_STATUS_"):
        raw = raw.replace("WORKFLOW_EXECUTION_STATUS_", "", 1)
    return raw


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def start_nmap_scan(
    target: str,
    nmap_args: str = "-sT --top-ports 100",
    label: str = "",
) -> dict[str, str]:
    """Start a background nmap scan via Temporal workflow.

    Returns immediately with a scan_id you can use to check status and
    retrieve results later.

    Args:
        target: Host, IP, or CIDR to scan (e.g. "scanme.nmap.org", "192.168.1.0/24")
        nmap_args: Nmap flags/options (default: "-sT --top-ports 100")
        label: Optional human-friendly label for this scan
    """
    client = await _get_client()
    scan_id = f"scan-{uuid.uuid4().hex[:12]}"

    scan_input = NmapScanInput(
        target=target,
        nmap_args=nmap_args,
        scan_id=scan_id,
    )

    await client.start_workflow(
        NmapScanWorkflow.run,
        scan_input,
        id=scan_id,
        task_queue=TASK_QUEUE,
    )

    _scan_registry[scan_id] = {
        "target": target,
        "nmap_args": nmap_args,
        "label": label or f"Scan of {target}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "RUNNING",
    }

    return {
        "scan_id": scan_id,
        "status": "RUNNING",
        "message": (
            f"Scan started. Use check_scan_status('{scan_id}') to poll "
            f"and get_scan_results('{scan_id}') when complete."
        ),
    }


@mcp.tool()
async def check_scan_status(scan_id: str) -> dict[str, Any]:
    """Check the current status of a running or completed scan.

    Args:
        scan_id: The scan identifier returned by start_nmap_scan()
    """
    client = await _get_client()

    handle = client.get_workflow_handle(scan_id)
    desc = await handle.describe()

    status_name = _normalize_workflow_status(desc.status)

    if scan_id in _scan_registry:
        _scan_registry[scan_id]["status"] = status_name

    result: dict[str, Any] = {
        "scan_id": scan_id,
        "status": status_name,
    }

    if scan_id in _scan_registry:
        result["label"] = _scan_registry[scan_id].get("label", "")
        result["target"] = _scan_registry[scan_id].get("target", "")
        result["started_at"] = _scan_registry[scan_id].get("started_at", "")

    if status_name == "COMPLETED":
        result["message"] = (
            f"Scan finished! Use get_scan_results('{scan_id}') to retrieve results."
        )
    elif status_name == "FAILED":
        result["message"] = "Scan failed. Check Temporal UI at :8080 for details."
    elif status_name == "RUNNING":
        result["message"] = "Scan is still running. Check again shortly."
    else:
        result["message"] = f"Workflow status: {status_name}"

    return result


@mcp.tool()
async def get_scan_results(scan_id: str) -> dict[str, Any]:
    """Fetch the parsed results of a completed scan.

    Returns structured data including per-host open ports, services,
    OS detection, script output, and a human-readable summary.

    Args:
        scan_id: The scan identifier returned by start_nmap_scan()
    """
    client = await _get_client()
    handle = client.get_workflow_handle(scan_id)

    desc = await handle.describe()
    status_name = _normalize_workflow_status(desc.status)

    if status_name != "COMPLETED":
        return {
            "scan_id": scan_id,
            "status": status_name,
            "error": (
                f"Scan is not yet complete (status: {status_name}). "
                "Use check_scan_status() to poll."
            ),
        }

    result = await handle.result()

    if scan_id in _scan_registry:
        _scan_registry[scan_id]["status"] = "COMPLETED"

    return {
        "scan_id": scan_id,
        "status": "COMPLETED",
        **result,
    }


@mcp.tool()
async def run_quick_scan(
    target: str,
    nmap_args: str = "-sT --top-ports 20",
) -> dict[str, Any]:
    """Run a fast nmap scan and wait for the result (blocking).

    Convenience method for quick scans. For longer scans, use
    start_nmap_scan() instead to avoid timeouts.

    Args:
        target: Host, IP, or CIDR to scan
        nmap_args: Nmap flags/options (default: "-sT --top-ports 20")
    """
    client = await _get_client()
    scan_id = f"scan-{uuid.uuid4().hex[:12]}"

    scan_input = NmapScanInput(
        target=target,
        nmap_args=nmap_args,
        scan_id=scan_id,
    )

    _scan_registry[scan_id] = {
        "target": target,
        "nmap_args": nmap_args,
        "label": f"Quick scan of {target}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "RUNNING",
    }

    result = await client.execute_workflow(
        NmapScanWorkflow.run,
        scan_input,
        id=scan_id,
        task_queue=TASK_QUEUE,
    )

    _scan_registry[scan_id]["status"] = "COMPLETED"

    return {
        "scan_id": scan_id,
        "status": "COMPLETED",
        **result,
    }


@mcp.tool()
async def list_recent_scans() -> dict[str, Any]:
    """List all scans started in this session with their current status.

    Useful for keeping track of multiple concurrent scans.
    """
    client = await _get_client()

    scans: list[dict[str, Any]] = []
    for scan_id, meta in _scan_registry.items():
        entry = {"scan_id": scan_id, **meta}

        # Refresh status from Temporal for non-terminal states
        if meta.get("status") in ("RUNNING", "UNKNOWN"):
            try:
                handle = client.get_workflow_handle(scan_id)
                desc = await handle.describe()
                status_name = _normalize_workflow_status(desc.status)
                entry["status"] = status_name
                _scan_registry[scan_id]["status"] = status_name
            except Exception:
                entry["status"] = "UNKNOWN"

        scans.append(entry)

    return {
        "total": len(scans),
        "scans": scans,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--stdio" in sys.argv:
        # stdio mode for mcpo proxy or direct MCP client
        mcp.run(transport="stdio")
    else:
        # Streamable HTTP mode (default)
        mcp.run(transport="streamable-http")
