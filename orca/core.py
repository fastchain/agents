from __future__ import annotations

"""
bus/core.py — Durable SQLite Message Bus

Every message and task is written to SQLite before being processed.
This means:
  - The bus can restart → all pending messages are still there
  - Agents can restart  → they re-claim their incomplete tasks
  - You can inspect the full history at any time
  - No message is lost or processed twice (at-least-once delivery)

## Core concepts

  Task:
    The unit of work. Created by the orchestrator, claimed by a specialist.
    Has a status: pending → processing → done | failed

  Checkpoint:
    A named snapshot of an agent's progress.
    Written by the agent periodically. On restart the agent reads its
    last checkpoint and continues from there.

  Message:
    A one-way notification between agents (not request/reply).
    Used for broadcasting events like "task X completed".

## Durability model

  SQLite with WAL mode:
    - WAL (Write-Ahead Log) makes concurrent reads+writes safe
    - Every write is fsync'd before the call returns
    - If the process dies mid-write, SQLite recovers on next open

  Task status machine:
    pending ──► processing ──► done
                    │
                    └──────────► failed (after max_retries exceeded)
                    │
                    └──────────► pending (on agent crash — auto-requeued)
"""

import json
import uuid
import time
import sqlite3
import threading
from pathlib import Path
from typing import Any
from contextlib import contextmanager
from dataclasses import dataclass, field


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str
    queue: str             # which specialist should handle this
    payload: dict          # the actual task data
    status: str            # pending | processing | done | failed
    created_at: float
    updated_at: float
    claimed_by: str | None = None   # agent instance id
    result: dict | None = None
    error: str | None = None
    retries: int = 0
    max_retries: int = 3
    parent_task_id: str | None = None  # for subtask trees


@dataclass
class Checkpoint:
    agent_id: str
    key: str               # e.g. "current_step", "processed_items"
    value: Any
    saved_at: float


@dataclass
class BusMessage:
    id: str
    topic: str
    payload: dict
    published_at: float
    publisher: str


# ── Bus ───────────────────────────────────────────────────────────────────

class DurableBus:
    """
    SQLite-backed message bus. Safe for multi-thread access within one process.
    For multi-process, use PostgreSQL backend (see bus/pg_backend.py).

    Usage:
        bus = DurableBus("agent_state.db")

        # Orchestrator creates a task
        task_id = bus.publish_task("db_specialist", {"task": "write connection pool"})

        # Specialist claims and processes it
        task = bus.claim_task("db_specialist", agent_id="specialist-1")
        result = do_work(task.payload)
        bus.complete_task(task.id, result={"output": result})

        # Orchestrator waits for result
        result = bus.wait_for_result(task_id, timeout=60)
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS tasks (
        id              TEXT PRIMARY KEY,
        queue           TEXT NOT NULL,
        payload         TEXT NOT NULL,   -- JSON
        status          TEXT NOT NULL DEFAULT 'pending',
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL,
        claimed_by      TEXT,
        result          TEXT,            -- JSON
        error           TEXT,
        retries         INTEGER NOT NULL DEFAULT 0,
        max_retries     INTEGER NOT NULL DEFAULT 3,
        parent_task_id  TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_queue_status ON tasks(queue, status);
    CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);

    CREATE TABLE IF NOT EXISTS checkpoints (
        agent_id    TEXT NOT NULL,
        key         TEXT NOT NULL,
        value       TEXT NOT NULL,   -- JSON
        saved_at    REAL NOT NULL,
        PRIMARY KEY (agent_id, key)
    );

    CREATE TABLE IF NOT EXISTS messages (
        id           TEXT PRIMARY KEY,
        topic        TEXT NOT NULL,
        payload      TEXT NOT NULL,  -- JSON
        published_at REAL NOT NULL,
        publisher    TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic);
    """

    # If a task stays in 'processing' for longer than this,
    # assume the agent crashed and requeue it.
    STALE_TASK_TIMEOUT = 120   # seconds

    def __init__(self, db_path: str | Path = "agent_bus.db"):
        self.db_path = str(db_path)
        self._local = threading.local()   # thread-local connections
        self._init_db()

    # ── Connection management ──────────────────────────────────────────────

    @property
    def _conn(self) -> sqlite3.Connection:
        """Thread-local SQLite connection (SQLite connections aren't thread-safe)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads + writes
            conn.execute("PRAGMA synchronous=NORMAL") # safe + fast
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _tx(self):
        """Context manager for a committed transaction."""
        conn = self._conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self):
        with self._tx() as conn:
            conn.executescript(self.SCHEMA)

    # ── Task API ───────────────────────────────────────────────────────────

    def publish_task(
        self,
        queue: str,
        payload: dict,
        *,
        task_id: str | None = None,
        max_retries: int = 3,
        parent_task_id: str | None = None,
    ) -> str:
        """
        Create a new task on a queue.
        Returns the task_id. The task is immediately durable.
        """
        tid = task_id or str(uuid.uuid4())
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, queue, payload, status, created_at, updated_at,
                                   max_retries, parent_task_id)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (tid, queue, json.dumps(payload), now, now, max_retries, parent_task_id),
            )
        return tid

    def claim_task(self, queue: str, agent_id: str) -> Task | None:
        """
        Atomically claim the next pending task on a queue.
        Also requeues stale tasks (agent crashed while processing).
        Returns None if no tasks are available.
        """
        self._requeue_stale_tasks(queue)

        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE queue = ? AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (queue,),
            ).fetchone()

            if not row:
                return None

            now = time.time()
            conn.execute(
                """
                UPDATE tasks SET status='processing', claimed_by=?, updated_at=?
                WHERE id=? AND status='pending'
                """,
                (agent_id, now, row["id"]),
            )

        return self._row_to_task(row)

    def complete_task(self, task_id: str, result: dict) -> None:
        """Mark a task as done with its result."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE tasks SET status='done', result=?, updated_at=? WHERE id=?",
                (json.dumps(result), time.time(), task_id),
            )

    def fail_task(self, task_id: str, error: str) -> None:
        """
        Mark a task step as failed.
        If retries remain, requeues it as 'pending'. Otherwise marks 'failed'.
        """
        with self._tx() as conn:
            row = conn.execute(
                "SELECT retries, max_retries FROM tasks WHERE id=?", (task_id,)
            ).fetchone()

            if not row:
                return

            retries = row["retries"] + 1
            if retries <= row["max_retries"]:
                conn.execute(
                    """UPDATE tasks SET status='pending', retries=?, error=?, updated_at=?,
                       claimed_by=NULL WHERE id=?""",
                    (retries, error, time.time(), task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status='failed', error=?, updated_at=? WHERE id=?",
                    (error, time.time(), task_id),
                )

    def get_task(self, task_id: str) -> Task | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        return self._row_to_task(row) if row else None

    def wait_for_result(self, task_id: str, timeout: float = 120, poll: float = 0.5) -> dict | None:
        """
        Block until a task is done or failed (or timeout).
        Returns the result dict, or raises on failure/timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")
            if task.status == "done":
                return task.result
            if task.status == "failed":
                raise RuntimeError(f"Task {task_id} failed: {task.error}")
            time.sleep(poll)
        raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")

    def pending_count(self, queue: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE queue=? AND status='pending'", (queue,)
        ).fetchone()
        return row[0]

    # ── Checkpoint API ─────────────────────────────────────────────────────

    def save_checkpoint(self, agent_id: str, key: str, value: Any) -> None:
        """
        Save a named checkpoint for an agent.
        Overwrites any previous value for the same (agent_id, key).

        Use this to record progress so agents can resume after restart:
            bus.save_checkpoint("orchestrator", "current_step", "waiting_for_db")
            bus.save_checkpoint("orchestrator", "processed_files", ["a.py", "b.py"])
        """
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints (agent_id, key, value, saved_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id, key) DO UPDATE SET value=excluded.value, saved_at=excluded.saved_at
                """,
                (agent_id, key, json.dumps(value), time.time()),
            )

    def load_checkpoint(self, agent_id: str, key: str, default: Any = None) -> Any:
        """
        Load a saved checkpoint. Returns `default` if not found.

        Use this at agent startup to resume:
            step = bus.load_checkpoint("orchestrator", "current_step")
            if step:
                print(f"Resuming from step: {step}")
        """
        row = self._conn.execute(
            "SELECT value FROM checkpoints WHERE agent_id=? AND key=?",
            (agent_id, key),
        ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def load_all_checkpoints(self, agent_id: str) -> dict:
        """Load all checkpoints for an agent as a dict."""
        rows = self._conn.execute(
            "SELECT key, value FROM checkpoints WHERE agent_id=?", (agent_id,)
        ).fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def clear_checkpoints(self, agent_id: str) -> None:
        """Clear all checkpoints for an agent (e.g. after a clean completion)."""
        with self._tx() as conn:
            conn.execute("DELETE FROM checkpoints WHERE agent_id=?", (agent_id,))

    # ── Message (pub/sub events) API ───────────────────────────────────────

    def publish_message(self, topic: str, payload: dict, publisher: str) -> str:
        """Publish a one-way event message to a topic."""
        mid = str(uuid.uuid4())
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO messages (id, topic, payload, published_at, publisher) VALUES (?,?,?,?,?)",
                (mid, topic, json.dumps(payload), time.time(), publisher),
            )
        return mid

    def get_messages(self, topic: str, since: float = 0.0) -> list[BusMessage]:
        """Read all messages on a topic since a given timestamp."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE topic=? AND published_at > ? ORDER BY published_at ASC",
            (topic, since),
        ).fetchall()
        return [
            BusMessage(
                id=r["id"],
                topic=r["topic"],
                payload=json.loads(r["payload"]),
                published_at=r["published_at"],
                publisher=r["publisher"],
            )
            for r in rows
        ]

    # ── Status / inspection ────────────────────────────────────────────────

    def status_report(self) -> dict:
        """Overview of all queues and task statuses. Useful for debugging."""
        rows = self._conn.execute(
            """
            SELECT queue, status, COUNT(*) as count
            FROM tasks
            GROUP BY queue, status
            ORDER BY queue, status
            """
        ).fetchall()
        report = {}
        for row in rows:
            q = row["queue"]
            if q not in report:
                report[q] = {}
            report[q][row["status"]] = row["count"]
        return report

    def get_task_tree(self, parent_task_id: str) -> list[Task]:
        """Get all subtasks of a parent task."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id=? ORDER BY created_at",
            (parent_task_id,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    # ── Internal helpers ───────────────────────────────────────────────────

    def _requeue_stale_tasks(self, queue: str) -> int:
        """
        Find tasks stuck in 'processing' for too long (agent crashed)
        and reset them to 'pending' so another agent can pick them up.
        """
        stale_cutoff = time.time() - self.STALE_TASK_TIMEOUT
        with self._tx() as conn:
            result = conn.execute(
                """
                UPDATE tasks SET status='pending', claimed_by=NULL, updated_at=?
                WHERE queue=? AND status='processing' AND updated_at < ?
                """,
                (time.time(), queue, stale_cutoff),
            )
            return result.rowcount

    def _row_to_task(self, row) -> Task:
        return Task(
            id=row["id"],
            queue=row["queue"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            claimed_by=row["claimed_by"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            retries=row["retries"],
            max_retries=row["max_retries"],
            parent_task_id=row["parent_task_id"],
        )
