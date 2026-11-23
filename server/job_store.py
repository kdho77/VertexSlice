"""Job store for /v1/slice/jobs.

The worker calls enqueue, claim, update, get, and result. claim moves the oldest
queued job to running in one atomic step, so two processes cannot take the same
job. The default store is in-memory and is the one tests and a single process
use. VERTEXSLICE_JOB_STORE=redis or postgres selects a shared store. Nothing
here opens a connection unless that store is selected.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_RETENTION_S = 60 * 60
_PREFIX = "vertexslice:slice_jobs"
_FIFO_KEY = f"{_PREFIX}:fifo"
_RUNNING_KEY = f"{_PREFIX}:running"
_HASH_PREFIX = f"{_PREFIX}:"

# One Redis script. LPOP and the status write commit together.
REDIS_CLAIM_LUA = """
local id = redis.call('LPOP', KEYS[1])
if not id then
  return ''
end
local key = ARGV[1] .. id
if redis.call('HGET', key, 'status') ~= 'queued' then
  return ''
end
redis.call('HSET', key, 'status', 'running', 'started_at', ARGV[2], 'claim_token', ARGV[3])
redis.call('SADD', KEYS[2], id)
return id
"""

REDIS_ENQUEUE_LUA = """
local key = KEYS[1]
local job_id = ARGV[1]
local extra = tonumber(ARGV[2])
for i = 3, #ARGV, 2 do
  redis.call('HSET', key, ARGV[i], ARGV[i + 1])
end
redis.call('RPUSH', KEYS[2], job_id)
return redis.call('LLEN', KEYS[2]) + redis.call('SCARD', KEYS[3]) + extra
"""

REDIS_UPDATE_LUA = """
local key = KEYS[1]
if redis.call('HGET', key, 'claim_token') ~= ARGV[1] then
  return 0
end
if redis.call('HGET', key, 'status') ~= 'running' then
  return 0
end
redis.call('HSET', key, 'status', ARGV[2], 'error', ARGV[3], 'gcode', ARGV[4], 'headers_json', ARGV[5], 'finished_at', ARGV[6])
redis.call('HDEL', key, 'model_b64')
redis.call('SREM', KEYS[2], ARGV[7])
local ttl = tonumber(ARGV[8])
if ttl > 0 then
  redis.call('EXPIRE', key, ttl)
end
return 1
"""

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vertexslice_slice_jobs (
    job_id text PRIMARY KEY,
    status text NOT NULL,
    queued_at timestamptz NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    error text,
    claim_token text,
    profile_id text NOT NULL,
    material_id text NOT NULL,
    suffix text NOT NULL,
    filename text,
    overrides_json text NOT NULL,
    belt_insole_place boolean NOT NULL,
    timeout_s integer NOT NULL,
    model_bytes bytea,
    gcode text,
    headers_json text
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS vertexslice_slice_jobs_queue
ON vertexslice_slice_jobs (queued_at, job_id)
WHERE status = 'queued'
"""

# Single statement. SKIP LOCKED is what keeps two workers off the same row.
POSTGRES_CLAIM_SQL = """
UPDATE vertexslice_slice_jobs AS job
SET status = 'running', started_at = %s, claim_token = %s
WHERE job.job_id = (
    SELECT candidate.job_id
    FROM vertexslice_slice_jobs AS candidate
    WHERE candidate.status = 'queued'
    ORDER BY candidate.queued_at, candidate.job_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING job_id, status, queued_at, started_at, finished_at, error, claim_token,
          profile_id, material_id, suffix, filename, overrides_json, belt_insole_place,
          timeout_s, model_bytes, gcode, headers_json
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    text = dt.astimezone(timezone.utc).isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def retention_from_env() -> int:
    """Finished jobs stay at least 60 minutes. A lower env value is raised to that floor."""
    raw = os.environ.get("VERTEXSLICE_JOB_RETENTION_S", "").strip()
    if not raw:
        return DEFAULT_RETENTION_S
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_S
    if value < DEFAULT_RETENTION_S:
        return DEFAULT_RETENTION_S
    return value


@dataclass
class StoredJob:
    job_id: str
    queued_at: datetime
    status: str = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    finished_mono: float | None = None
    error: str | None = None
    claim_token: str | None = None
    profile_id: str = ""
    material_id: str = ""
    suffix: str = ".stl"
    filename: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    belt_insole_place: bool = True
    timeout_s: int = 260
    model_bytes: bytes | None = None
    gcode: str | None = None
    headers: dict[str, str] | None = None

    def fields(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "queued_at": _iso(self.queued_at) or "",
            "started_at": _iso(self.started_at) or "",
            "finished_at": _iso(self.finished_at) or "",
            "error": self.error or "",
            "claim_token": self.claim_token or "",
            "profile_id": self.profile_id,
            "material_id": self.material_id,
            "suffix": self.suffix,
            "filename": self.filename or "",
            "overrides_json": json.dumps(self.overrides, separators=(",", ":"), sort_keys=True),
            "belt_insole_place": "1" if self.belt_insole_place else "0",
            "timeout_s": str(int(self.timeout_s)),
            "model_b64": base64.b64encode(self.model_bytes or b"").decode("ascii"),
            "gcode": self.gcode or "",
            "headers_json": json.dumps(self.headers or {}, separators=(",", ":"), sort_keys=True),
        }

    @classmethod
    def from_fields(cls, fields: dict[str, str]) -> StoredJob:
        model_b64 = fields.get("model_b64") or ""
        model = base64.b64decode(model_b64.encode("ascii")) if model_b64 else None
        headers_raw = fields.get("headers_json") or ""
        headers = json.loads(headers_raw) if headers_raw else None
        overrides_raw = fields.get("overrides_json") or "{}"
        return cls(
            job_id=fields["job_id"],
            queued_at=_parse_iso(fields.get("queued_at")) or _utcnow(),
            status=fields.get("status") or "queued",
            started_at=_parse_iso(fields.get("started_at")),
            finished_at=_parse_iso(fields.get("finished_at")),
            error=fields.get("error") or None,
            claim_token=fields.get("claim_token") or None,
            profile_id=fields.get("profile_id") or "",
            material_id=fields.get("material_id") or "",
            suffix=fields.get("suffix") or ".stl",
            filename=fields.get("filename") or None,
            overrides=json.loads(overrides_raw),
            belt_insole_place=fields.get("belt_insole_place") == "1",
            timeout_s=int(fields.get("timeout_s") or "260"),
            model_bytes=model,
            gcode=fields.get("gcode") or None,
            headers={str(key): str(value) for key, value in headers.items()} if headers else None,
        )


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


def _view_for(job: StoredJob, position: int | None, now: datetime | None = None) -> JobView:
    elapsed = None
    if job.started_at is not None:
        end = job.finished_at or now or _utcnow()
        elapsed = round((end - job.started_at).total_seconds(), 3)
    error = job.error if job.status == "failed" else None
    return JobView(
        job_id=job.job_id,
        status=job.status,
        position=position,
        queued_at=_iso(job.queued_at) or "",
        started_at=_iso(job.started_at),
        finished_at=_iso(job.finished_at),
        elapsed_s=elapsed,
        error=error,
        gcode=job.gcode,
        headers=dict(job.headers) if job.headers is not None else None,
    )


class _Waiters:
    def __init__(self) -> None:
        self._items: list[threading.Condition] = []
        self._lock = threading.Lock()

    def add(self, cv: threading.Condition) -> None:
        with self._lock:
            if cv not in self._items:
                self._items.append(cv)

    def wake(self) -> None:
        with self._lock:
            items = list(self._items)
        for cv in items:
            with cv:
                cv.notify_all()


class JobStore:
    """enqueue / claim / update / get / result. claim is atomic on every implementation."""

    def __init__(self) -> None:
        self.retention_s = DEFAULT_RETENTION_S
        self._waiters = _Waiters()

    def add_waiter(self, cv: threading.Condition) -> None:
        self._waiters.add(cv)

    def wake(self) -> None:
        self._waiters.wake()

    def enqueue(self, job: StoredJob, local_extra: int = 0) -> int:
        raise NotImplementedError

    def claim(self) -> StoredJob | None:
        """Take the oldest queued job, or return None. A job is claimed by at most one caller."""
        raise NotImplementedError

    def update(
        self,
        job_id: str,
        claim_token: str,
        *,
        status: str,
        error: str | None,
        gcode: str | None,
        headers: dict[str, str] | None,
        finished_at: datetime,
    ) -> None:
        raise NotImplementedError

    def get(self, job_id: str, local_extra: int = 0) -> JobView | None:
        raise NotImplementedError

    def result(self, job_id: str, local_extra: int = 0) -> JobView | None:
        return self.get(job_id, local_extra=local_extra)

    def oldest_queued_at(self) -> datetime | None:
        raise NotImplementedError

    def has_pending(self) -> bool:
        raise NotImplementedError

    def ids(self) -> list[str]:
        raise NotImplementedError

    def reset(self) -> None:
        raise RuntimeError("reset is only implemented for the in-memory job store")


class MemoryJobStore(JobStore):
    """Process-local FIFO. claim holds the store lock across the queued-to-running change."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._jobs: dict[str, StoredJob] = {}
        self._fifo: deque[str] = deque()
        self._last_queued: datetime | None = None

    def enqueue(self, job: StoredJob, local_extra: int = 0) -> int:
        with self._lock:
            self._purge_locked()
            job.queued_at = self._next_queued_locked()
            job.status = "queued"
            self._jobs[job.job_id] = job
            self._fifo.append(job.job_id)
            position = self._index_locked(job.job_id) + self._running_locked() + local_extra + 1
        self.wake()
        return position

    def claim(self) -> StoredJob | None:
        with self._lock:
            while self._fifo:
                job_id = self._fifo.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                job.status = "running"
                job.started_at = _utcnow()
                job.claim_token = uuid.uuid4().hex
                return job
            return None

    def update(
        self,
        job_id: str,
        claim_token: str,
        *,
        status: str,
        error: str | None,
        gcode: str | None,
        headers: dict[str, str] | None,
        finished_at: datetime,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.claim_token != claim_token or job.status != "running":
                raise RuntimeError(f"lost claim on slice job {job_id}")
            job.status = status
            job.error = error
            job.gcode = gcode
            job.headers = dict(headers) if headers is not None else None
            job.finished_at = finished_at
            job.finished_mono = time.monotonic()
            job.model_bytes = None

    def get(self, job_id: str, local_extra: int = 0) -> JobView | None:
        with self._lock:
            self._purge_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            position = None
            if job.status == "queued":
                position = self._index_locked(job.job_id) + self._running_locked() + local_extra + 1
            return _view_for(job, position)

    def oldest_queued_at(self) -> datetime | None:
        with self._lock:
            for job_id in self._fifo:
                job = self._jobs.get(job_id)
                if job is not None and job.status == "queued":
                    return job.queued_at
            return None

    def has_pending(self) -> bool:
        with self._lock:
            return any(job.status in ("queued", "running") for job in self._jobs.values())

    def ids(self) -> list[str]:
        with self._lock:
            self._purge_locked()
            return list(self._jobs)

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._fifo.clear()
            self._last_queued = None
            self.retention_s = DEFAULT_RETENTION_S

    def _next_queued_locked(self) -> datetime:
        now = _utcnow()
        if self._last_queued is not None and now <= self._last_queued:
            now = self._last_queued + timedelta(microseconds=1)
        self._last_queued = now
        return now

    def _index_locked(self, job_id: str) -> int:
        try:
            return self._fifo.index(job_id)
        except ValueError:
            return 0

    def _running_locked(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status == "running")

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
            job.model_bytes = None


def _hash_key(job_id: str) -> str:
    return _HASH_PREFIX + job_id


class RedisJobStore(JobStore):
    """Shared FIFO. claim is one EVAL, so two replicas cannot pop the same id."""

    def __init__(self, url: str, client: Any = None) -> None:
        super().__init__()
        self.retention_s = retention_from_env()
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError("VERTEXSLICE_JOB_STORE=redis requires the redis package") from exc
            client = redis.Redis.from_url(url, decode_responses=True)
        self._client = client

    def enqueue(self, job: StoredJob, local_extra: int = 0) -> int:
        job.queued_at = _utcnow()
        job.status = "queued"
        fields = job.fields()
        args: list[str] = [job.job_id, str(int(local_extra))]
        for key, value in fields.items():
            args.append(key)
            args.append(value)
        position = self._client.eval(
            REDIS_ENQUEUE_LUA,
            3,
            _hash_key(job.job_id),
            _FIFO_KEY,
            _RUNNING_KEY,
            *args,
        )
        self.wake()
        return int(position)

    def claim(self) -> StoredJob | None:
        token = uuid.uuid4().hex
        started = _iso(_utcnow()) or ""
        job_id = self._client.eval(
            REDIS_CLAIM_LUA,
            2,
            _FIFO_KEY,
            _RUNNING_KEY,
            _HASH_PREFIX,
            started,
            token,
        )
        if not job_id:
            return None
        fields = self._client.hgetall(_hash_key(str(job_id)))
        if not fields:
            return None
        return StoredJob.from_fields(fields)

    def update(
        self,
        job_id: str,
        claim_token: str,
        *,
        status: str,
        error: str | None,
        gcode: str | None,
        headers: dict[str, str] | None,
        finished_at: datetime,
    ) -> None:
        ttl = int(self.retention_s)
        wrote = self._client.eval(
            REDIS_UPDATE_LUA,
            2,
            _hash_key(job_id),
            _RUNNING_KEY,
            claim_token,
            status,
            error or "",
            gcode or "",
            json.dumps(headers or {}, separators=(",", ":"), sort_keys=True),
            _iso(finished_at) or "",
            job_id,
            str(ttl),
        )
        if int(wrote or 0) != 1:
            raise RuntimeError(f"lost claim on slice job {job_id}")

    def get(self, job_id: str, local_extra: int = 0) -> JobView | None:
        fields = self._client.hgetall(_hash_key(job_id))
        if not fields:
            return None
        job = StoredJob.from_fields(fields)
        position = None
        if job.status == "queued":
            index = self._client.lpos(_FIFO_KEY, job_id)
            running = int(self._client.scard(_RUNNING_KEY) or 0)
            position = int(index or 0) + running + local_extra + 1
        return _view_for(job, position)

    def oldest_queued_at(self) -> datetime | None:
        job_id = self._client.lindex(_FIFO_KEY, 0)
        if not job_id:
            return None
        raw = self._client.hget(_hash_key(str(job_id)), "queued_at")
        return _parse_iso(raw if isinstance(raw, str) else None)

    def has_pending(self) -> bool:
        if int(self._client.llen(_FIFO_KEY) or 0) > 0:
            return True
        return int(self._client.scard(_RUNNING_KEY) or 0) > 0

    def ids(self) -> list[str]:
        found: list[str] = []
        for key in self._client.scan_iter(match=_HASH_PREFIX + "*", count=200):
            text = str(key)
            if text in (_FIFO_KEY, _RUNNING_KEY):
                continue
            found.append(text[len(_HASH_PREFIX):])
        return found


class PostgresJobStore(JobStore):
    """Shared FIFO. claim is one UPDATE ... FOR UPDATE SKIP LOCKED."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self.retention_s = retention_from_env()
        self._url = url
        self._lock = threading.Lock()
        self._conn: Any = None
        self._ready = False

    def enqueue(self, job: StoredJob, local_extra: int = 0) -> int:
        job.queued_at = _utcnow()
        job.status = "queued"
        with self._lock:
            self._ensure()
            assert self._conn is not None
            with self._conn.transaction():
                self._purge_tx()
                self._conn.execute(
                    """
                    INSERT INTO vertexslice_slice_jobs (
                        job_id, status, queued_at, profile_id, material_id, suffix, filename,
                        overrides_json, belt_insole_place, timeout_s, model_bytes, gcode, headers_json
                    ) VALUES (%s, 'queued', %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL)
                    """,
                    (
                        job.job_id,
                        job.queued_at,
                        job.profile_id,
                        job.material_id,
                        job.suffix,
                        job.filename,
                        json.dumps(job.overrides, separators=(",", ":"), sort_keys=True),
                        job.belt_insole_place,
                        int(job.timeout_s),
                        job.model_bytes,
                    ),
                )
                row = self._conn.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM vertexslice_slice_jobs
                         WHERE status = 'queued' AND (queued_at, job_id) <= (%s, %s))
                      + (SELECT COUNT(*) FROM vertexslice_slice_jobs WHERE status = 'running')
                      + %s
                    """,
                    (job.queued_at, job.job_id, int(local_extra)),
                ).fetchone()
            position = int(row[0])
        self.wake()
        return position

    def claim(self) -> StoredJob | None:
        token = uuid.uuid4().hex
        started = _utcnow()
        with self._lock:
            self._ensure()
            assert self._conn is not None
            with self._conn.transaction():
                row = self._conn.execute(POSTGRES_CLAIM_SQL, (started, token)).fetchone()
            if row is None:
                return None
            return _job_from_row(row)

    def update(
        self,
        job_id: str,
        claim_token: str,
        *,
        status: str,
        error: str | None,
        gcode: str | None,
        headers: dict[str, str] | None,
        finished_at: datetime,
    ) -> None:
        with self._lock:
            self._ensure()
            assert self._conn is not None
            with self._conn.transaction():
                row = self._conn.execute(
                    """
                    UPDATE vertexslice_slice_jobs
                    SET status = %s, error = %s, gcode = %s, headers_json = %s,
                        finished_at = %s, model_bytes = NULL
                    WHERE job_id = %s AND claim_token = %s AND status = 'running'
                    RETURNING job_id
                    """,
                    (
                        status,
                        error,
                        gcode,
                        json.dumps(headers, separators=(",", ":"), sort_keys=True) if headers is not None else None,
                        finished_at,
                        job_id,
                        claim_token,
                    ),
                ).fetchone()
            if row is None:
                raise RuntimeError(f"lost claim on slice job {job_id}")

    def get(self, job_id: str, local_extra: int = 0) -> JobView | None:
        with self._lock:
            self._ensure()
            assert self._conn is not None
            with self._conn.transaction():
                self._purge_tx()
                row = self._conn.execute(
                    """
                    SELECT job_id, status, queued_at, started_at, finished_at, error, claim_token,
                           profile_id, material_id, suffix, filename, overrides_json, belt_insole_place,
                           timeout_s, model_bytes, gcode, headers_json
                    FROM vertexslice_slice_jobs WHERE job_id = %s
                    """,
                    (job_id,),
                ).fetchone()
                if row is None:
                    return None
                job = _job_from_row(row)
                position = None
                if job.status == "queued":
                    ahead = self._conn.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM vertexslice_slice_jobs
                             WHERE status = 'queued' AND (queued_at, job_id) < (%s, %s))
                          + (SELECT COUNT(*) FROM vertexslice_slice_jobs WHERE status = 'running')
                          + %s
                        """,
                        (job.queued_at, job.job_id, int(local_extra)),
                    ).fetchone()
                    position = int(ahead[0]) + 1
            return _view_for(job, position)

    def oldest_queued_at(self) -> datetime | None:
        with self._lock:
            self._ensure()
            assert self._conn is not None
            row = self._conn.execute(
                """
                SELECT queued_at FROM vertexslice_slice_jobs
                WHERE status = 'queued'
                ORDER BY queued_at, job_id
                LIMIT 1
                """
            ).fetchone()
            self._conn.commit()
            if row is None or row[0] is None:
                return None
            queued_at = row[0]
            if queued_at.tzinfo is None:
                return queued_at.replace(tzinfo=timezone.utc)
            return queued_at.astimezone(timezone.utc)

    def has_pending(self) -> bool:
        with self._lock:
            self._ensure()
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT 1 FROM vertexslice_slice_jobs WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone()
            self._conn.commit()
            return row is not None

    def ids(self) -> list[str]:
        with self._lock:
            self._ensure()
            assert self._conn is not None
            with self._conn.transaction():
                self._purge_tx()
                rows = self._conn.execute(
                    "SELECT job_id FROM vertexslice_slice_jobs ORDER BY queued_at, job_id"
                ).fetchall()
            return [row[0] for row in rows]

    def _ensure(self) -> None:
        if self._ready and self._conn is not None and not self._conn.closed:
            return
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("VERTEXSLICE_JOB_STORE=postgres requires the psycopg package") from exc
        self._conn = psycopg.connect(self._url)
        self._conn.execute(_CREATE_SQL)
        self._conn.execute(_CREATE_INDEX_SQL)
        self._conn.commit()
        self._ready = True

    def _purge_tx(self) -> None:
        assert self._conn is not None
        cutoff = _utcnow() - timedelta(seconds=self.retention_s)
        self._conn.execute(
            """
            DELETE FROM vertexslice_slice_jobs
            WHERE finished_at IS NOT NULL AND finished_at <= %s
            """,
            (cutoff,),
        )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _job_from_row(row: tuple) -> StoredJob:
    (
        job_id, status, queued_at, started_at, finished_at, error, claim_token,
        profile_id, material_id, suffix, filename, overrides_json, belt, timeout_s,
        model_bytes, gcode, headers_json,
    ) = row
    if isinstance(model_bytes, memoryview):
        model_bytes = bytes(model_bytes)
    headers = json.loads(headers_json) if headers_json else None
    return StoredJob(
        job_id=job_id,
        queued_at=_aware(queued_at) or _utcnow(),
        status=status,
        started_at=_aware(started_at),
        finished_at=_aware(finished_at),
        error=error,
        claim_token=claim_token,
        profile_id=profile_id,
        material_id=material_id,
        suffix=suffix,
        filename=filename,
        overrides=json.loads(overrides_json or "{}"),
        belt_insole_place=bool(belt),
        timeout_s=int(timeout_s),
        model_bytes=model_bytes,
        gcode=gcode,
        headers={str(key): str(value) for key, value in headers.items()} if headers else None,
    )


def job_store_from_env() -> JobStore:
    """memory (default), redis, or postgres. The shared stores are not contacted in memory mode."""
    kind = os.environ.get("VERTEXSLICE_JOB_STORE", "memory").strip().lower() or "memory"
    if kind == "memory":
        store: JobStore = MemoryJobStore()
        store.retention_s = retention_from_env()
        return store
    if kind == "redis":
        url = os.environ.get("VERTEXSLICE_REDIS_URL", "").strip()
        if not url:
            raise RuntimeError("VERTEXSLICE_JOB_STORE=redis requires VERTEXSLICE_REDIS_URL")
        return RedisJobStore(url)
    if kind == "postgres":
        url = os.environ.get("VERTEXSLICE_JOB_DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError("VERTEXSLICE_JOB_STORE=postgres requires VERTEXSLICE_JOB_DATABASE_URL")
        return PostgresJobStore(url)
    raise RuntimeError(
        f"Unknown VERTEXSLICE_JOB_STORE={kind!r}. Use memory, redis, or postgres."
    )
