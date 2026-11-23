"""Single in-process FIFO queue for /v1/slice and /v1/slice/jobs.

One worker thread runs the existing slice function off the asyncio event loop.
Job submissions return as soon as the upload is valid. The synchronous route
waits on the same queue, so a second slice never starts until the first one
leaves the worker. Disconnecting a sync client cancels that slice's subprocess
tree (Linux /proc) and frees the worker for the next item.

Finished jobs stay in memory for at least an hour. A restart drops them.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException

DEFAULT_RETENTION_S = 60 * 60


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
    job: SliceJob | None = None
    cancel: threading.Event = None  # type: ignore[assignment]
    done: threading.Event = None  # type: ignore[assignment]
    text: str | None = None
    headers: dict[str, str] | None = None
    error: BaseException | None = None
    killer_started: bool = False

    def __post_init__(self) -> None:
        if self.cancel is None:
            self.cancel = threading.Event()
        if self.done is None:
            self.done = threading.Event()


@dataclass(eq=False)
class SliceJob:
    job_id: str
    queued_at: datetime
    position_at_accept: int
    status: str = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    finished_mono: float | None = None
    error: str | None = None
    gcode: str | None = None
    headers: dict[str, str] | None = None

    def mark_running(self) -> None:
        self.status = "running"
        self.started_at = _utcnow()

    def mark_finished(self, item: WorkItem) -> None:
        self.finished_at = _utcnow()
        self.finished_mono = time.monotonic()
        if item.error is None:
            self.status = "succeeded"
            self.gcode = item.text
            self.headers = dict(item.headers or {})
            self.error = None
            return
        self.status = "failed"
        self.error = error_text(item.error)
        self.gcode = None
        self.headers = None


@dataclass
class JobView:
    job_id: str
    status: str
    position: int | None
    queued_at: str
    started_at: str | None
    finished_at: str | None
    elapsed_s: float | None
    error: str | None
    gcode: str | None = None
    headers: dict[str, str] | None = None

    def public(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "position": self.position,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": self.elapsed_s,
            "error": self.error,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    text = dt.astimezone(timezone.utc).isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


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


class SliceJobQueue:
    def __init__(self, runner: Callable[[PreparedSlice], tuple[str, dict[str, str]]]):
        self._runner = runner
        self.retention_s = DEFAULT_RETENTION_S
        self._jobs: dict[str, SliceJob] = {}
        self._queue: deque[WorkItem] = deque()
        self._current: WorkItem | None = None
        self._current_pids: set[int] | None = None
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        with self._cv:
            if self._started:
                return
            self._thread = threading.Thread(target=self._loop, name="slice-job-worker", daemon=True)
            self._thread.start()
            self._started = True

    def submit_sync(self, prepared: PreparedSlice) -> WorkItem:
        self.start()
        item = WorkItem(prepared=prepared, job=None)
        with self._cv:
            self._queue.append(item)
            self._cv.notify()
        return item

    def enqueue(self, prepared: PreparedSlice, job_id: str) -> tuple[str, int]:
        """Accept a job. Returns (job_id, position) captured at acceptance.

        Position is 1-based and counts this job, every unfinished job ahead of
        it, and a slice already running (including a synchronous one).
        """
        self.start()
        job = SliceJob(job_id=job_id, queued_at=_utcnow(), position_at_accept=1)
        item = WorkItem(prepared=prepared, job=job)
        with self._cv:
            self._purge_locked()
            self._jobs[job_id] = job
            self._queue.append(item)
            position = self._position_locked(job)
            if position is None:
                position = 1
            job.position_at_accept = position
            self._cv.notify()
        return job_id, position

    def cancel(self, item: WorkItem) -> None:
        """Drop a queued sync slice, or kill the subprocess tree if it is running."""
        item.cancel.set()
        with self._cv:
            if item in self._queue:
                self._queue.remove(item)
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
            self._purge_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._view_locked(job)

    def known_ids(self) -> list[str]:
        with self._cv:
            self._purge_locked()
            return list(self._jobs)

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while (self._current is not None or self._queue) and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=remaining)
            return self._current is None and not self._queue

    def reset_for_tests(self) -> None:
        with self._cv:
            queued = list(self._queue)
            self._queue.clear()
            current = self._current
            self._jobs.clear()
            self.retention_s = DEFAULT_RETENTION_S
        for item in queued:
            item.cancel.set()
            if item.error is None:
                item.error = RequestCancelled()
            item.done.set()
        if current is not None:
            self.cancel(current)
            current.done.wait(timeout=5)
        self.wait_idle(2)

    def _arm_killer(self, item: WorkItem, before: set[int]) -> None:
        def _run() -> None:
            while not item.done.is_set():
                kill_new_descendants(os.getpid(), before)
                item.done.wait(0.05)
            kill_new_descendants(os.getpid(), before)

        threading.Thread(target=_run, name="slice-cancel", daemon=True).start()

    def _loop(self) -> None:
        while True:
            item = self._take()
            if item is None:
                continue
            try:
                self._execute(item)
            except Exception as exc:
                item.error = item.error or exc
                item.done.set()
                with self._cv:
                    if self._current is item:
                        self._current = None
                        self._current_pids = None
                    self._cv.notify_all()

    def _take(self) -> WorkItem | None:
        with self._cv:
            while not self._queue:
                self._cv.wait()
            item = self._queue.popleft()
            if item.cancel.is_set():
                if item.error is None:
                    item.error = RequestCancelled()
                item.done.set()
                self._cv.notify_all()
                return None
            self._current = item
            self._current_pids = set(descendant_pids(os.getpid()))
            if item.job is not None:
                item.job.mark_running()
            return item

    def _execute(self, item: WorkItem) -> None:
        try:
            if item.cancel.is_set():
                item.error = RequestCancelled()
                return
            prepared = item.prepared
            if prepared is None:
                item.error = RequestCancelled()
                return
            text, headers = self._runner(prepared)
            item.text = text
            item.headers = headers
        except Exception as exc:
            item.error = exc
        finally:
            item.prepared = None
            if item.error is None and item.text is None and not item.cancel.is_set():
                item.error = RuntimeError("slice produced no gcode")
            with self._cv:
                if item.job is not None and not isinstance(item.error, RequestCancelled):
                    item.job.mark_finished(item)
                if self._current is item:
                    self._current = None
                    self._current_pids = None
                self._cv.notify_all()
            item.done.set()

    def _position_locked(self, job: SliceJob) -> int | None:
        if job.status != "queued":
            return None
        ahead = 1 if self._current is not None else 0
        for item in self._queue:
            if item.job is job:
                return ahead + 1
            ahead += 1
        return None

    def _elapsed_locked(self, job: SliceJob) -> float | None:
        if job.started_at is None:
            return None
        end = job.finished_at or _utcnow()
        return round((end - job.started_at).total_seconds(), 3)

    def _view_locked(self, job: SliceJob) -> JobView:
        return JobView(
            job_id=job.job_id,
            status=job.status,
            position=self._position_locked(job),
            queued_at=_iso(job.queued_at) or "",
            started_at=_iso(job.started_at),
            finished_at=_iso(job.finished_at),
            elapsed_s=self._elapsed_locked(job),
            error=job.error,
            gcode=job.gcode,
            headers=dict(job.headers) if job.headers is not None else None,
        )

    def _purge_locked(self) -> None:
        now = time.monotonic()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_mono is not None and (now - job.finished_mono) >= self.retention_s
        ]
        for job_id in expired:
            job = self._jobs.pop(job_id)
            job.gcode = None
            job.headers = None
