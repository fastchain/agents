"""Temporal workflows and activities for nmap scanning."""

from __future__ import annotations

import asyncio
import re
import shlex
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


@dataclass
class NmapScanInput:
    target: str
    nmap_args: str
    scan_id: str


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

BLOCKED_METACHARACTERS = re.compile(r"[;&|`$(){}<>\n\r]")
BLOCKED_FLAGS = {
    "--script-args",
    "-il",
    "-on",
    "-ox",
    "-og",
    "-oa",
    "--datadir",
}


@activity.defn
async def validate_scan_input(scan_input: NmapScanInput) -> bool:
    """Sanitize scan input to prevent command injection.

    Raises on invalid input so the workflow fails fast.
    """
    target = scan_input.target
    args = scan_input.nmap_args

    # Check target for metacharacters
    if BLOCKED_METACHARACTERS.search(target):
        raise ValueError(
            f"Target contains blocked characters: {target!r}"
        )

    # Check args for metacharacters
    if BLOCKED_METACHARACTERS.search(args):
        raise ValueError(
            f"Arguments contain blocked characters: {args!r}"
        )

    # Parse with shlex to verify they are well-formed shell tokens
    try:
        tokens = shlex.split(args) if args.strip() else []
    except ValueError as exc:
        raise ValueError(f"Arguments are not parseable: {exc}") from exc

    # Check each token against blocked flags
    for token in tokens:
        canonical = token.split("=", 1)[0].lower()  # handle --flag=value
        if canonical in BLOCKED_FLAGS:
            raise ValueError(f"Blocked nmap flag: {canonical!r}")

    # Verify target is not empty
    if not target.strip():
        raise ValueError("Target must not be empty")

    activity.logger.info(
        "Input validated for scan %s: target=%s args=%s",
        scan_input.scan_id,
        target,
        args,
    )
    return True


@activity.defn
async def run_nmap_scan(scan_input: NmapScanInput) -> str:
    """Execute nmap as an async subprocess and return raw XML output.

    Heartbeats every 10 seconds to keep Temporal informed that the
    long-running activity is still alive.
    """
    args_tokens = shlex.split(scan_input.nmap_args) if scan_input.nmap_args.strip() else []
    cmd = ["nmap", *args_tokens, "-oX", "-", scan_input.target]

    activity.logger.info("Running: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Read stdout/stderr concurrently while heartbeating
    async def _read_stream(stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    stdout_task = asyncio.create_task(_read_stream(proc.stdout))  # type: ignore[arg-type]
    stderr_task = asyncio.create_task(_read_stream(proc.stderr))  # type: ignore[arg-type]

    # Heartbeat loop — runs until the process finishes
    while not stdout_task.done() or not stderr_task.done():
        activity.heartbeat(f"nmap running for scan {scan_input.scan_id}")
        await asyncio.sleep(10)

    stdout_bytes = await stdout_task
    stderr_bytes = await stderr_task
    return_code = await proc.wait()

    stdout_str = stdout_bytes.decode("utf-8", errors="replace")
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    if return_code != 0 and not stdout_str.strip():
        raise RuntimeError(
            f"nmap exited with code {return_code}. stderr: {stderr_str}"
        )

    if stderr_str.strip():
        activity.logger.warning("nmap stderr: %s", stderr_str)

    activity.logger.info(
        "nmap finished for scan %s (exit %d, %d bytes XML)",
        scan_input.scan_id,
        return_code,
        len(stdout_str),
    )
    return stdout_str


@activity.defn
async def parse_nmap_xml(raw_xml: str) -> dict[str, Any]:
    """Parse nmap XML output into a structured dict."""
    root = ET.fromstring(raw_xml)

    # --- Scan info ---
    scan_info: dict[str, Any] = {
        "scanner": root.get("scanner", "nmap"),
        "args": root.get("args", ""),
        "start_time": root.get("startstr", ""),
        "xml_version": root.get("xmloutputversion", ""),
    }

    run_stats = root.find("runstats")
    if run_stats is not None:
        finished = run_stats.find("finished")
        if finished is not None:
            scan_info["end_time"] = finished.get("timestr", "")
            scan_info["elapsed"] = finished.get("elapsed", "")
        hosts_elem = run_stats.find("hosts")
        if hosts_elem is not None:
            scan_info["hosts_up"] = int(hosts_elem.get("up", 0))
            scan_info["hosts_down"] = int(hosts_elem.get("down", 0))
            scan_info["hosts_total"] = int(hosts_elem.get("total", 0))

    # --- Per-host data ---
    hosts: list[dict[str, Any]] = []

    for host_elem in root.findall("host"):
        host: dict[str, Any] = {}

        # Status
        status_elem = host_elem.find("status")
        if status_elem is not None:
            host["status"] = status_elem.get("state", "unknown")

        # Addresses
        host["addresses"] = []
        for addr in host_elem.findall("address"):
            host["addresses"].append({
                "addr": addr.get("addr", ""),
                "type": addr.get("addrtype", ""),
                "vendor": addr.get("vendor", ""),
            })

        # Hostnames
        host["hostnames"] = []
        hostnames_elem = host_elem.find("hostnames")
        if hostnames_elem is not None:
            for hn in hostnames_elem.findall("hostname"):
                host["hostnames"].append({
                    "name": hn.get("name", ""),
                    "type": hn.get("type", ""),
                })

        # Ports
        host["ports"] = []
        ports_elem = host_elem.find("ports")
        if ports_elem is not None:
            for port_elem in ports_elem.findall("port"):
                port_data: dict[str, Any] = {
                    "port": int(port_elem.get("portid", 0)),
                    "protocol": port_elem.get("protocol", ""),
                }
                state_elem = port_elem.find("state")
                if state_elem is not None:
                    port_data["state"] = state_elem.get("state", "")
                    port_data["reason"] = state_elem.get("reason", "")

                service_elem = port_elem.find("service")
                if service_elem is not None:
                    port_data["service"] = service_elem.get("name", "")
                    port_data["product"] = service_elem.get("product", "")
                    port_data["version"] = service_elem.get("version", "")
                    port_data["extra_info"] = service_elem.get("extrainfo", "")

                # Script output
                scripts: list[dict[str, str]] = []
                for script_elem in port_elem.findall("script"):
                    scripts.append({
                        "id": script_elem.get("id", ""),
                        "output": script_elem.get("output", ""),
                    })
                if scripts:
                    port_data["scripts"] = scripts

                host["ports"].append(port_data)

        # OS matches
        host["os_matches"] = []
        os_elem = host_elem.find("os")
        if os_elem is not None:
            for osmatch in os_elem.findall("osmatch"):
                host["os_matches"].append({
                    "name": osmatch.get("name", ""),
                    "accuracy": osmatch.get("accuracy", ""),
                })

        # Host scripts
        host_scripts: list[dict[str, str]] = []
        hostscript_elem = host_elem.find("hostscript")
        if hostscript_elem is not None:
            for script_elem in hostscript_elem.findall("script"):
                host_scripts.append({
                    "id": script_elem.get("id", ""),
                    "output": script_elem.get("output", ""),
                })
        if host_scripts:
            host["host_scripts"] = host_scripts

        # Open ports summary
        open_ports = [
            p for p in host["ports"] if p.get("state") == "open"
        ]
        host["open_ports_summary"] = ", ".join(
            f"{p['port']}/{p['protocol']} ({p.get('service', 'unknown')})"
            for p in open_ports
        )

        hosts.append(host)

    # --- Human-readable summary ---
    summary_parts: list[str] = []
    summary_parts.append(
        f"Nmap scan completed. "
        f"{scan_info.get('hosts_up', '?')} host(s) up out of "
        f"{scan_info.get('hosts_total', '?')} scanned."
    )

    for i, h in enumerate(hosts):
        addr_str = ", ".join(a["addr"] for a in h.get("addresses", []))
        hostname_str = ", ".join(
            hn["name"] for hn in h.get("hostnames", []) if hn["name"]
        )
        host_label = addr_str
        if hostname_str:
            host_label += f" ({hostname_str})"

        summary_parts.append(f"\nHost {i + 1}: {host_label} [{h.get('status', '?')}]")

        if h.get("open_ports_summary"):
            summary_parts.append(f"  Open ports: {h['open_ports_summary']}")
        else:
            summary_parts.append("  No open ports found.")

        for osm in h.get("os_matches", [])[:3]:
            summary_parts.append(
                f"  OS guess: {osm['name']} ({osm['accuracy']}% accuracy)"
            )

    summary = "\n".join(summary_parts)

    return {
        "scan_info": scan_info,
        "hosts": hosts,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

@workflow.defn
class NmapScanWorkflow:
    """Orchestrates an nmap scan: validate → run → parse."""

    @workflow.run
    async def run(self, scan_input: NmapScanInput) -> dict[str, Any]:
        # Step 1: Validate (fail fast, no retries)
        await workflow.execute_activity(
            validate_scan_input,
            scan_input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

        # Step 2: Run nmap (long-running, heartbeat, retries)
        raw_xml = await workflow.execute_activity(
            run_nmap_scan,
            scan_input,
            start_to_close_timeout=timedelta(hours=4),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # Step 3: Parse XML output
        result = await workflow.execute_activity(
            parse_nmap_xml,
            raw_xml,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        return result
