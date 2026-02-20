"""Temporal worker that executes shell command workflows and activities."""

from __future__ import annotations

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from workflows import (
    ShellCommandWorkflow,
    format_output,
    run_shell_command,
    validate_command,
)

TASK_QUEUE = "shell-tasks"
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")
TEMPORAL_CONNECT_RETRIES = 60
TEMPORAL_RETRY_DELAY_SECONDS = 2


async def _connect_temporal_with_retry(logger: logging.Logger) -> Client:
    last_exc: Exception | None = None
    for attempt in range(1, TEMPORAL_CONNECT_RETRIES + 1):
        try:
            logger.info(
                "Connecting to Temporal at %s (attempt %d/%d)",
                TEMPORAL_HOST,
                attempt,
                TEMPORAL_CONNECT_RETRIES,
            )
            return await Client.connect(TEMPORAL_HOST)
        except Exception as exc:
            last_exc = exc
            if attempt == TEMPORAL_CONNECT_RETRIES:
                break
            await asyncio.sleep(TEMPORAL_RETRY_DELAY_SECONDS)
    raise RuntimeError(
        f"Failed to connect to Temporal at {TEMPORAL_HOST} "
        f"after {TEMPORAL_CONNECT_RETRIES} attempts"
    ) from last_exc


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("worker")

    client = await _connect_temporal_with_retry(logger)

    logger.info("Starting worker on task queue %r", TASK_QUEUE)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ShellCommandWorkflow],
        activities=[validate_command, run_shell_command, format_output],
    )

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
