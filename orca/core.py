from __future__ import annotations

"""
IRC-backed bus.

Queues are IRC channels on a server (default localhost:6667). Task state and
checkpoints are synchronized by broadcasting structured events over IRC.
"""

import base64
import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class Task:
    id: str
    queue: str
    payload: dict
    status: str
    created_at: float
    updated_at: float
    claimed_by: str | None = None
    result: dict | None = None
    error: str | None = None
    retries: int = 0
    max_retries: int = 3
    parent_task_id: str | None = None


@dataclass
class Checkpoint:
    agent_id: str
    key: str
    value: Any
    saved_at: float


@dataclass
class BusMessage:
    id: str
    topic: str
    payload: dict
    published_at: float
    publisher: str


class DurableBus:
    """
    IRC-backed bus preserving the previous DurableBus API shape.

    Notes:
    - `db_path` is accepted for backwards compatibility but ignored.
    - Queue names map to IRC channels.
    - Uses event synchronization over IRC PRIVMSG.
    """

    STALE_TASK_TIMEOUT = 120
    CONTROL_CHANNEL = "#bus_control"
    CHECKPOINT_CHANNEL = "#bus_checkpoints"
    MESSAGE_CHANNEL = "#bus_messages"

    def __init__(
        self,
        db_path: str | None = None,
        *,
        irc_host: str = "localhost",
        irc_port: int = 6667,
        nickname: str | None = None,
    ):
        _ = db_path
        self.irc_host = irc_host
        self.irc_port = irc_port
        self.nickname = nickname or f"bus_{uuid.uuid4().hex[:8]}"

        self._tasks: dict[str, Task] = {}
        self._checkpoints: dict[tuple[str, str], Checkpoint] = {}
        self._messages: list[BusMessage] = []
        self._joined_channels: set[str] = set()
        self._chunk_buffer: dict[str, dict[int, str]] = {}
        self._chunk_expected: dict[str, int] = {}

        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._ready = threading.Event()
        self._stop = threading.Event()

        self._sock = socket.create_connection((self.irc_host, self.irc_port), timeout=10)
        self._sock.settimeout(0.5)
        self._recv_buffer = b""

        self._send_raw(f"NICK {self.nickname}")
        self._send_raw(f"USER {self.nickname} 0 * :{self.nickname}")

        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

        # Give connection a short window to register and start reading.
        self._ready.wait(timeout=2.0)

        self._ensure_channel(self.CONTROL_CHANNEL)
        self._ensure_channel(self.CHECKPOINT_CHANNEL)
        self._ensure_channel(self.MESSAGE_CHANNEL)

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def publish_task(
        self,
        queue: str,
        payload: dict,
        *,
        task_id: str | None = None,
        max_retries: int = 3,
        parent_task_id: str | None = None,
    ) -> str:
        tid = task_id or str(uuid.uuid4())
        now = time.time()
        event = {
            "type": "task_create",
            "task": {
                "id": tid,
                "queue": queue,
                "payload": payload,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "claimed_by": None,
                "result": None,
                "error": None,
                "retries": 0,
                "max_retries": max_retries,
                "parent_task_id": parent_task_id,
            },
        }
        self._send_event(self._queue_channel(queue), event)
        self._wait_until(lambda: tid in self._tasks, timeout=2.0)
        return tid

    def claim_task(self, queue: str, agent_id: str) -> Task | None:
        self._ensure_channel(self._queue_channel(queue))
        self._requeue_stale_tasks(queue)

        with self._lock:
            candidates = [
                t for t in self._tasks.values() if t.queue == queue and t.status == "pending"
            ]
            candidates.sort(key=lambda t: (t.created_at, t.id))

        for task in candidates:
            event = {
                "type": "task_claim",
                "task_id": task.id,
                "agent_id": agent_id,
                "updated_at": time.time(),
            }
            self._send_event(self._queue_channel(queue), event)

            self._wait_until(
                lambda: self._is_claimed_by(task.id, agent_id) or self._is_not_pending(task.id),
                timeout=1.0,
            )

            claimed = self.get_task(task.id)
            if claimed and claimed.status == "processing" and claimed.claimed_by == agent_id:
                return claimed

        return None

    def complete_task(self, task_id: str, result: dict) -> None:
        task = self.get_task(task_id)
        if not task:
            return
        event = {
            "type": "task_complete",
            "task_id": task_id,
            "result": result,
            "updated_at": time.time(),
        }
        self._send_event(self._queue_channel(task.queue), event)
        self._wait_until(lambda: self._task_status(task_id) == "done", timeout=2.0)

    def fail_task(self, task_id: str, error: str) -> None:
        task = self.get_task(task_id)
        if not task:
            return
        event = {
            "type": "task_fail",
            "task_id": task_id,
            "error": error,
            "updated_at": time.time(),
        }
        self._send_event(self._queue_channel(task.queue), event)

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return Task(**task.__dict__)

    def wait_for_result(self, task_id: str, timeout: float = 120, poll: float = 0.5) -> dict | None:
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
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.queue == queue and t.status == "pending")

    def save_checkpoint(self, agent_id: str, key: str, value: Any) -> None:
        event = {
            "type": "checkpoint_set",
            "agent_id": agent_id,
            "key": key,
            "value": value,
            "saved_at": time.time(),
        }
        self._send_event(self.CHECKPOINT_CHANNEL, event)
        self._wait_until(lambda: (agent_id, key) in self._checkpoints, timeout=2.0)

    def load_checkpoint(self, agent_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            cp = self._checkpoints.get((agent_id, key))
            return cp.value if cp else default

    def load_all_checkpoints(self, agent_id: str) -> dict:
        with self._lock:
            return {cp.key: cp.value for cp in self._checkpoints.values() if cp.agent_id == agent_id}

    def clear_checkpoints(self, agent_id: str) -> None:
        # Best-effort local clear. Propagating deletes would need a dedicated event.
        with self._lock:
            keys = [k for k in self._checkpoints if k[0] == agent_id]
            for key in keys:
                del self._checkpoints[key]

    def publish_message(self, topic: str, payload: dict, publisher: str) -> str:
        mid = str(uuid.uuid4())
        event = {
            "type": "message_publish",
            "id": mid,
            "topic": topic,
            "payload": payload,
            "published_at": time.time(),
            "publisher": publisher,
        }
        self._send_event(self.MESSAGE_CHANNEL, event)
        return mid

    def get_messages(self, topic: str, since: float = 0.0) -> list[BusMessage]:
        with self._lock:
            return [m for m in self._messages if m.topic == topic and m.published_at > since]

    def status_report(self) -> dict:
        report: dict[str, dict[str, int]] = {}
        with self._lock:
            for task in self._tasks.values():
                queue_stats = report.setdefault(task.queue, {})
                queue_stats[task.status] = queue_stats.get(task.status, 0) + 1
        return report

    def get_task_tree(self, parent_task_id: str) -> list[Task]:
        with self._lock:
            return [Task(**t.__dict__) for t in self._tasks.values() if t.parent_task_id == parent_task_id]

    def list_tasks(
        self,
        *,
        queue: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        if queue is not None:
            tasks = [t for t in tasks if t.queue == queue]
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [Task(**t.__dict__) for t in tasks[:limit]]

    def list_checkpoints(self, agent_id: str | None = None) -> list[Checkpoint]:
        with self._lock:
            cps = list(self._checkpoints.values())
        if agent_id is not None:
            cps = [cp for cp in cps if cp.agent_id == agent_id]
        cps.sort(key=lambda cp: cp.saved_at, reverse=True)
        return [Checkpoint(**cp.__dict__) for cp in cps]

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _queue_channel(self, queue: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in queue)
        if not safe:
            safe = "default"
        channel = f"#q_{safe}"
        self._ensure_channel(channel)
        return channel

    def _ensure_channel(self, channel: str) -> None:
        with self._lock:
            if channel in self._joined_channels:
                return
            self._joined_channels.add(channel)
        self._send_raw(f"JOIN {channel}")

    def _send_event(self, channel: str, event: dict) -> None:
        self._ensure_channel(channel)
        payload = json.dumps(event, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        b64 = base64.urlsafe_b64encode(payload).decode("ascii")

        # Keep margin under IRC line length limits.
        chunk_size = 300
        if len(b64) <= chunk_size:
            self._send_raw(f"PRIVMSG {channel} :BUS1 {b64}")
            return

        msg_id = uuid.uuid4().hex[:10]
        total = (len(b64) + chunk_size - 1) // chunk_size
        for idx in range(total):
            start = idx * chunk_size
            end = start + chunk_size
            piece = b64[start:end]
            self._send_raw(f"PRIVMSG {channel} :BUSC {msg_id} {idx + 1}/{total} {piece}")

    def _send_raw(self, line: str) -> None:
        data = (line + "\r\n").encode("utf-8", errors="ignore")
        with self._send_lock:
            self._sock.sendall(data)

    def _reader_loop(self) -> None:
        self._ready.set()
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    time.sleep(0.1)
                    continue
                self._recv_buffer += chunk

                while b"\r\n" in self._recv_buffer:
                    line, self._recv_buffer = self._recv_buffer.split(b"\r\n", 1)
                    self._handle_line(line.decode("utf-8", errors="ignore"))
            except socket.timeout:
                continue
            except Exception:
                time.sleep(0.1)

    def _handle_line(self, line: str) -> None:
        if line.startswith("PING"):
            token = line.split(":", 1)[1] if ":" in line else ""
            self._send_raw(f"PONG :{token}")
            return

        if " PRIVMSG " not in line:
            return

        try:
            prefix, rest = line.split(" PRIVMSG ", 1)
            channel, text = rest.split(" :", 1)
        except ValueError:
            return

        if text.startswith("BUS1 "):
            b64 = text[5:].strip()
            self._apply_b64_event(b64)
            return

        if text.startswith("BUSC "):
            parts = text.split(" ", 3)
            if len(parts) != 4:
                return
            _, msg_id, seq, piece = parts
            if "/" not in seq:
                return
            idx_str, total_str = seq.split("/", 1)
            try:
                idx = int(idx_str)
                total = int(total_str)
            except ValueError:
                return

            with self._lock:
                if msg_id not in self._chunk_buffer:
                    self._chunk_buffer[msg_id] = {}
                    self._chunk_expected[msg_id] = total
                self._chunk_buffer[msg_id][idx] = piece

                if len(self._chunk_buffer[msg_id]) == self._chunk_expected[msg_id]:
                    ordered = "".join(self._chunk_buffer[msg_id][i] for i in range(1, total + 1))
                    del self._chunk_buffer[msg_id]
                    del self._chunk_expected[msg_id]
                    self._apply_b64_event(ordered)

    def _apply_b64_event(self, b64_payload: str) -> None:
        try:
            raw = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
            event = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        self._apply_event(event)

    def _apply_event(self, event: dict) -> None:
        event_type = event.get("type")
        with self._lock:
            if event_type == "task_create":
                t = event["task"]
                self._tasks[t["id"]] = Task(**t)
                return

            if event_type == "task_claim":
                task = self._tasks.get(event["task_id"])
                if task and task.status == "pending":
                    task.status = "processing"
                    task.claimed_by = event["agent_id"]
                    task.updated_at = event.get("updated_at", time.time())
                return

            if event_type == "task_complete":
                task = self._tasks.get(event["task_id"])
                if task:
                    task.status = "done"
                    task.result = event.get("result")
                    task.updated_at = event.get("updated_at", time.time())
                return

            if event_type == "task_fail":
                task = self._tasks.get(event["task_id"])
                if task:
                    retries = task.retries + 1
                    task.retries = retries
                    task.error = event.get("error")
                    task.updated_at = event.get("updated_at", time.time())
                    if retries <= task.max_retries:
                        task.status = "pending"
                        task.claimed_by = None
                    else:
                        task.status = "failed"
                return

            if event_type == "checkpoint_set":
                cp = Checkpoint(
                    agent_id=event["agent_id"],
                    key=event["key"],
                    value=event["value"],
                    saved_at=event.get("saved_at", time.time()),
                )
                self._checkpoints[(cp.agent_id, cp.key)] = cp
                return

            if event_type == "message_publish":
                self._messages.append(
                    BusMessage(
                        id=event["id"],
                        topic=event["topic"],
                        payload=event["payload"],
                        published_at=event.get("published_at", time.time()),
                        publisher=event["publisher"],
                    )
                )

    def _requeue_stale_tasks(self, queue: str) -> int:
        stale_cutoff = time.time() - self.STALE_TASK_TIMEOUT
        changed = 0
        with self._lock:
            for task in self._tasks.values():
                if task.queue != queue:
                    continue
                if task.status == "processing" and task.updated_at < stale_cutoff:
                    task.status = "pending"
                    task.claimed_by = None
                    task.updated_at = time.time()
                    changed += 1
        return changed

    def _wait_until(self, predicate, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def _is_claimed_by(self, task_id: str, agent_id: str) -> bool:
        task = self._tasks.get(task_id)
        return bool(task and task.status == "processing" and task.claimed_by == agent_id)

    def _is_not_pending(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return bool(task and task.status != "pending")

    def _task_status(self, task_id: str) -> str | None:
        task = self._tasks.get(task_id)
        return task.status if task else None
