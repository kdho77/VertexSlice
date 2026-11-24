"""FIFO slice worker for /v1/slice and /v1/slice/jobs.

One thread per process runs the existing slice function off the asyncio event
loop. Job rows live in a JobStore. claim is atomic, so several processes can
share a Redis or Postgres store and still slice each job once. The default
store is in-memory. Disconnecting a sync client cancels that slice's subprocess
tree (Linux /proc) and frees this process's worker.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException

from server.job_store import (
    DEFAULT_RETENTION_S,
    JobStore,
    JobView,
    MemoryJobStore,
    StoredJob,
    job_store_from_env,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

__all__ = [
    "DEFAULT_RETENTION_S",
    "JobView",
    "PreparedSlice",
    "RequestCancelled",
    "SliceJobQueue",
    "descendant_pids",
    "error_text",
    "job_store_from_env",
    "kill_new_descendants",
]

_POLL_S = 0.25


class RequestCancelled(Exception):
    """The sync caller went away before this slice produced G-code."""


@dataclass
class PreparedSlice:
    model_bytes: bytes
    suffix: str
    filename: str | None
    profile_id: str
    material_id: str
    profile: object
    overrides: dict
    belt_insole_place: bool
    timeout_s: int


@dataclass(eq=False)
class WorkItem:
    prepared: PreparedSlice | None
    stored: StoredJob | None = None
    arrived_at: datetime | None = None
    cancel: threading.Event = None  # type: ignore[assignment]
    done: threading.Event = None  # type: ignore[assignment]
    text: str | None = None
    headers: dict[str, str] | None = None
    error: BaseException | None = None
    killer_started: bool = False
    stored_done: bool = False

    def __post_init__(self) -> None:
        if self.cancel is None:
            self.cancel = threading.Event()
        if self.done is None:
            self.done = threading.Event()
        if self.arrived_at is None:
            self.arrived_at = _utcnow()


def error_text(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, str):
            return detail
        return str(detail)
    return str(exc)


def _ppid(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", "r", encoding="ascii", errors="replace") as handle:
            for line in handle:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


def descendant_pids(root: int) -> list[int]:
    """Post-order process tree under root. Empty when /proc is unavailable."""
    if not os.path.isdir("/proc"):
        return []
    children: dict[int, list[int]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        parent = _ppid(pid)
        if parent is None:
            continue
        children.setdefault(parent, []).append(pid)
    ordered: list[int] = []

    def walk(pid: int) -> None:
        for child in children.get(pid, []):
            walk(child)
            ordered.append(child)

    walk(root)
    return ordered


def kill_new_descendants(root: int, before: set[int]) -> None:
    """SIGKILL processes that appeared under root after `before` was taken.

    Children are signalled before their parents. The slice function and the
    worker thread are not processes in this set, so a successful slice that
    is not cancelled is left alone.
    """
    me = os.getpid()
    fresh = [pid for pid in descendant_pids(root) if pid not in before and pid != me and pid > 1]
    for pid in fresh:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def stored_from_prepared(prepared: PreparedSlice, job_id: str) -> StoredJob:
    return StoredJob(
        job_id=job_id,
        queued_at=_utcnow(),
        profile_id=prepared.profile_id,
        material_id=prepared.material_id,
        suffix=prepared.suffix,
        filename=prepared.filename,
        overrides=dict(prepared.overrides),
        belt_insole_place=prepared.belt_insole_place,
        timeout_s=prepared.timeout_s,
        model_bytes=prepared.model_bytes,
    )


class SliceJobQueue:
    def __init__(
        self,
        runner: Callable[[PreparedSlice], tuple[str, dict[str, str]]],
        prepare: Callable[[StoredJob], PreparedSlice],
        store: JobStore | None = None,
    ):
        self._runner = runner
        self._prepare = prepare
        self._store = store if store is not None else MemoryJobStore()
        self._sync: list[WorkItem] = []
        self._current: WorkItem | None = None
        self._current_pids: set[int] | None = None
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def store(self) -> JobStore:
        return self._store

    @property
    def retention_s(self) -> int:
        return self._store.retention_s

    @retention_s.setter
    def retention_s(self, value: int) -> None:
        self._store.retention_s = value

    def start(self) -> None:
        with self._cv:
            if self._started:
                return
            self._store.add_waiter(self._cv)
            self._thread = threading.Thread(target=self._loop, name="slice-job-worker", daemon=True)
            self._thread.start()
            self._started = True

    def submit_sync(self, prepared: PreparedSlice) -> WorkItem:
        """Queue a slice whose HTTP response stays on this process."""
        self.start()
        item = WorkItem(prepared=prepared, stored=None, arrived_at=_utcnow())
        with self._cv:
            self._sync.append(item)
            self._cv.notify()
        self._store.wake()
        return item

    def enqueue(self, prepared: PreparedSlice, job_id: str) -> tuple[str, int]:
        """Accept a job. Returns (job_id, position) captured at acceptance.

        Position is 1-based and counts this job, every unfinished job ahead of
        it, and a slice already running on this process or recorded in the store.
        """
        self.start()
        job = stored_from_prepared(prepared, job_id)
        with self._cv:
            local_extra = self._local_extra_locked()
        position = self._store.enqueue(job, local_extra=local_extra)
        with self._cv:
            self._cv.notify()
        return job_id, position

    def cancel(self, item: WorkItem) -> None:
        """Drop a queued sync slice, or kill the subprocess tree if it is running."""
        item.cancel.set()
        with self._cv:
            if item in self._sync:
                self._sync.remove(item)
                if item.error is None:
                    item.error = RequestCancelled()
                item.done.set()
                self._cv.notify_all()
                return
            if self._current is not item or item.killer_started:
                return
            item.killer_started = True
            before = set(self._current_pids or ())
        self._arm_killer(item, before)

    def get(self, job_id: str) -> JobView | None:
        with self._cv:
            local_extra = self._local_extra_locked()
        return self._store.get(job_id, local_extra=local_extra)

    def result(self, job_id: str) -> JobView | None:
        with self._cv:
            local_extra = self._local_extra_locked()
        return self._store.result(job_id, local_extra=local_extra)

    def known_ids(self) -> list[str]:
        return self._store.ids()

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._busy_locked() and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=remaining)
            return not self._busy_locked()

    def reset_for_tests(self) -> None:
        with self._cv:
            queued = list(self._sync)
            self._sync.clear()
            current = self._current
        self._store.reset()
        for item in queued:
            item.cancel.set()
            if item.error is None:
                item.error = RequestCancelled()
            item.done.set()
        if current is not None and not current.done.is_set():
            self.cancel(current)
            current.done.wait(timeout=5)
        self.wait_idle(2)

    def _local_extra_locked(self) -> int:
        """A sync slice on this process is not a row in the store, so count it here."""
        current = self._current
        if current is not None and current.stored is None:
            return 1
        return 0

    def _busy_locked(self) -> bool:
        if self._current is not None or self._sync:
            return True
        return self._store.has_pending()

    def _arm_killer(self, item: WorkItem, before: set[int]) -> None:
        def _run() -> None:
            while not item.done.is_set():
                kill_new_descendants(os.getpid(), before)
                item.done.wait(0.05)
            kill_new_descendants(os.getpid(), before)

        threading.Thread(target=_run, name="slice-cancel", daemon=True).start()

    def _loop(self) -> None:
        while True:
            item = None
            try:
                item = self._take()
                if item is None:
                    continue
                self._execute(item)
            except Exception as exc:
                if item is not None:
                    item.error = item.error or exc
                    item.done.set()
                with self._cv:
                    if item is not None and self._current is item:
                        self._current = None
                        self._current_pids = None
                    self._cv.notify_all()

    def _take(self) -> WorkItem | None:
        while True:
            sync = self._claim_sync_if_due()
            if sync is not None:
                return sync
            claimed = self._store.claim()
            if claimed is not None:
                return self._adopt(claimed)
            with self._cv:
                if self._sync:
                    continue
                self._cv.wait(timeout=_POLL_S)

    def _claim_sync_if_due(self) -> WorkItem | None:
        with self._cv:
            if not self._sync:
                return None
            item = self._sync[0]
        oldest = self._store.oldest_queued_at()
        if oldest is not None and item.arrived_at is not None and oldest < item.arrived_at:
            return None
        with self._cv:
            if not self._sync or self._sync[0] is not item:
                return None
            self._sync.pop(0)
            if item.cancel.is_set():
                if item.error is None:
                    item.error = RequestCancelled()
                item.done.set()
                self._cv.notify_all()
                return None
            self._current = item
            self._current_pids = set(descendant_pids(os.getpid()))
            return item

    def _adopt(self, stored: StoredJob) -> WorkItem:
        item = WorkItem(prepared=None, stored=stored, arrived_at=stored.queued_at)
        with self._cv:
            self._current = item
            self._current_pids = set(descendant_pids(os.getpid()))
        return item

    def _execute(self, item: WorkItem) -> None:
        try:
            if item.cancel.is_set():
                item.error = RequestCancelled()
                return
            prepared = item.prepared
            if prepared is None:
                if item.stored is None:
                    item.error = RequestCancelled()
                    return
                prepared = self._prepare(item.stored)
            text, headers = self._runner(prepared)
            item.text = text
            item.headers = headers
        except Exception as exc:
            item.error = exc
        finally:
            item.prepared = None
            if item.error is None and item.text is None and not item.cancel.is_set():
                item.error = RuntimeError("slice produced no gcode")
            self._finish_stored(item)
            with self._cv:
                if self._current is item:
                    self._current = None
                    self._current_pids = None
                self._cv.notify_all()
            item.done.set()

    def _finish_stored(self, item: WorkItem) -> None:
        stored = item.stored
        if stored is None or stored.claim_token is None or item.stored_done:
            return
        if isinstance(item.error, RequestCancelled):
            return
        item.stored_done = True
        finished = _utcnow()
        if item.error is None:
            self._store.update(
                stored.job_id,
                stored.claim_token,
                status="succeeded",
                error=None,
                gcode=item.text,
                headers=dict(item.headers or {}),
                finished_at=finished,
            )
            return
        self._store.update(
            stored.job_id,
            stored.claim_token,
            status="failed",
            error=error_text(item.error),
            gcode=None,
            headers=None,
            finished_at=finished,
        )
