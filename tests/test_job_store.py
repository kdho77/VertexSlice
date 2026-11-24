"""Job store: one claim per job, memory by default, redis and postgres selected by env.

No Redis or Postgres server is started. The Redis class is exercised with an
in-process client whose EVAL holds one lock, which is the same atomic boundary
the Lua script has on a real server.
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from server.job_store import (
    POSTGRES_CLAIM_SQL,
    REDIS_CLAIM_LUA,
    REDIS_ENQUEUE_LUA,
    REDIS_UPDATE_LUA,
    MemoryJobStore,
    RedisJobStore,
    StoredJob,
    job_store_from_env,
    retention_from_env,
)
from server.jobs import PreparedSlice, SliceJobQueue


def _stored(job_id: str, payload: bytes) -> StoredJob:
    return StoredJob(
        job_id=job_id,
        queued_at=datetime.now(timezone.utc),
        profile_id="apex-v2-insole-95a",
        material_id="apex_core",
        suffix=".stl",
        filename=f"{job_id}.stl",
        overrides={"wall_loops": 2},
        belt_insole_place=True,
        timeout_s=260,
        model_bytes=payload,
    )


def _prepared(payload: bytes) -> PreparedSlice:
    return PreparedSlice(
        model_bytes=payload,
        suffix=".stl",
        filename="m.stl",
        profile_id="apex-v2-insole-95a",
        material_id="apex_core",
        profile=object(),
        overrides={"wall_loops": 2},
        belt_insole_place=True,
        timeout_s=260,
    )


def _prepare(stored: StoredJob) -> PreparedSlice:
    return PreparedSlice(
        model_bytes=stored.model_bytes or b"",
        suffix=stored.suffix,
        filename=stored.filename,
        profile_id=stored.profile_id,
        material_id=stored.material_id,
        profile=object(),
        overrides=dict(stored.overrides),
        belt_insole_place=stored.belt_insole_place,
        timeout_s=stored.timeout_s,
    )


class FakeRedis:
    """Just enough Redis for the three job scripts. Each EVAL is one critical section."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.claim_evals = 0

    def eval(self, script: str, numkeys: int, *values: str):
        keys = list(values[:numkeys])
        argv = list(values[numkeys:])
        with self._lock:
            if script == REDIS_ENQUEUE_LUA:
                return self._enqueue(keys, argv)
            if script == REDIS_CLAIM_LUA:
                self.claim_evals += 1
                return self._claim(keys, argv)
            if script == REDIS_UPDATE_LUA:
                return self._update(keys, argv)
        raise AssertionError("unexpected Redis script")

    def hgetall(self, key: str) -> dict[str, str]:
        with self._lock:
            return dict(self.hashes.get(key, {}))

    def hget(self, key: str, field: str) -> str | None:
        with self._lock:
            return self.hashes.get(key, {}).get(field)

    def lpos(self, key: str, value: str) -> int | None:
        with self._lock:
            try:
                return self.lists.get(key, []).index(value)
            except ValueError:
                return None

    def llen(self, key: str) -> int:
        with self._lock:
            return len(self.lists.get(key, []))

    def lindex(self, key: str, index: int) -> str | None:
        with self._lock:
            items = self.lists.get(key, [])
            if index < 0 or index >= len(items):
                return None
            return items[index]

    def scard(self, key: str) -> int:
        with self._lock:
            return len(self.sets.get(key, set()))

    def _enqueue(self, keys: list[str], argv: list[str]) -> int:
        hash_key, fifo, running = keys
        job_id = argv[0]
        extra = int(argv[1])
        fields: dict[str, str] = {}
        for index in range(2, len(argv), 2):
            fields[argv[index]] = argv[index + 1]
        self.hashes[hash_key] = fields
        self.lists.setdefault(fifo, []).append(job_id)
        running_n = len(self.sets.get(running, set()))
        return len(self.lists[fifo]) + running_n + extra

    def _claim(self, keys: list[str], argv: list[str]) -> str:
        fifo, running = keys
        prefix, started, token = argv
        items = self.lists.setdefault(fifo, [])
        if not items:
            return ""
        job_id = items.pop(0)
        fields = self.hashes.get(prefix + job_id)
        if fields is None or fields.get("status") != "queued":
            return ""
        fields["status"] = "running"
        fields["started_at"] = started
        fields["claim_token"] = token
        self.sets.setdefault(running, set()).add(job_id)
        return job_id

    def _update(self, keys: list[str], argv: list[str]) -> int:
        hash_key, running = keys
        token, status, error, gcode, headers_json, finished_at, job_id, _ttl = argv
        fields = self.hashes.get(hash_key)
        if fields is None or fields.get("claim_token") != token or fields.get("status") != "running":
            return 0
        fields["status"] = status
        fields["error"] = error
        fields["gcode"] = gcode
        fields["headers_json"] = headers_json
        fields["finished_at"] = finished_at
        fields.pop("model_b64", None)
        self.sets.get(running, set()).discard(job_id)
        return 1


class JobStoreTests(unittest.TestCase):
    def test_default_store_is_memory_and_shared_stores_need_a_url(self):
        from server.app import slice_queue

        self.assertIsInstance(slice_queue.store, MemoryJobStore)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VERTEXSLICE_JOB_STORE", None)
            self.assertIsInstance(job_store_from_env(), MemoryJobStore)
        with mock.patch.dict(os.environ, {"VERTEXSLICE_JOB_STORE": "redis"}, clear=False):
            os.environ.pop("VERTEXSLICE_REDIS_URL", None)
            with self.assertRaises(RuntimeError) as raised:
                job_store_from_env()
            self.assertIn("VERTEXSLICE_REDIS_URL", str(raised.exception))
        with mock.patch.dict(os.environ, {"VERTEXSLICE_JOB_STORE": "postgres"}, clear=False):
            os.environ.pop("VERTEXSLICE_JOB_DATABASE_URL", None)
            with self.assertRaises(RuntimeError) as raised:
                job_store_from_env()
            self.assertIn("VERTEXSLICE_JOB_DATABASE_URL", str(raised.exception))
        with mock.patch.dict(os.environ, {"VERTEXSLICE_JOB_STORE": "sqlite"}, clear=False):
            with self.assertRaises(RuntimeError):
                job_store_from_env()

    def test_retention_env_cannot_drop_finished_jobs_before_one_hour(self):
        with mock.patch.dict(os.environ, {"VERTEXSLICE_JOB_RETENTION_S": "10"}, clear=False):
            self.assertEqual(retention_from_env(), 3600)
        with mock.patch.dict(os.environ, {"VERTEXSLICE_JOB_RETENTION_S": "7200"}, clear=False):
            self.assertEqual(retention_from_env(), 7200)

    def test_memory_claims_are_exclusive_and_fifo(self):
        ordered = MemoryJobStore()
        fifo_ids = [f"f{index:02d}" for index in range(5)]
        for job_id in fifo_ids:
            ordered.enqueue(_stored(job_id, job_id.encode()), local_extra=0)
        claimed = []
        while True:
            job = ordered.claim()
            if job is None:
                break
            claimed.append(job.job_id)
        self.assertEqual(claimed, fifo_ids)

        store = MemoryJobStore()
        other = MemoryJobStore()
        ids = [f"{index:02d}" for index in range(40)]
        for job_id in ids:
            store.enqueue(_stored(job_id, job_id.encode()), local_extra=0)
        self.assertIsNone(other.claim())
        found: list[str] = []
        lock = threading.Lock()

        def take() -> None:
            while True:
                job = store.claim()
                if job is None:
                    return
                with lock:
                    found.append(job.job_id)

        threads = [threading.Thread(target=take) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(found), ids)
        self.assertEqual(len(found), len(set(found)))

    def test_claim_commands_are_single_atomic_steps(self):
        self.assertIn("FOR UPDATE SKIP LOCKED", POSTGRES_CLAIM_SQL)
        self.assertNotIn(";", POSTGRES_CLAIM_SQL)
        self.assertTrue(POSTGRES_CLAIM_SQL.lstrip().startswith("UPDATE"))
        self.assertEqual(POSTGRES_CLAIM_SQL.count("FOR UPDATE"), 1)
        self.assertEqual(REDIS_CLAIM_LUA.count("LPOP"), 1)
        self.assertIn("HSET", REDIS_CLAIM_LUA)
        self.assertIn("SADD", REDIS_CLAIM_LUA)
        source = RedisJobStore.claim.__doc__
        self.assertIsNone(source)
        import inspect

        body = inspect.getsource(RedisJobStore.claim)
        self.assertEqual(body.count(".eval("), 1)
        self.assertIn("REDIS_CLAIM_LUA", body)

    def test_two_workers_share_memory_without_slicing_twice(self):
        self._two_workers(MemoryJobStore())

    def test_two_workers_share_one_redis_claim_script(self):
        client = FakeRedis()
        store = RedisJobStore("redis://not-contacted/0", client=client)
        self._two_workers(store)
        self.assertGreaterEqual(client.claim_evals, 2)
        with self.assertRaises(RuntimeError):
            store.reset()

    def _two_workers(self, store) -> None:
        hold = threading.Event()
        seen: list[bytes] = []
        idents: set[int] = set()
        lock = threading.Lock()

        def runner(prepared: PreparedSlice):
            with lock:
                seen.append(bytes(prepared.model_bytes))
                idents.add(threading.get_ident())
            if not hold.wait(5):
                raise RuntimeError("second worker did not reach the slice")
            return ";shared\n", {"X-Profile-Id": prepared.profile_id}

        first = SliceJobQueue(runner, _prepare, store)
        second = SliceJobQueue(runner, _prepare, store)
        first.start()
        second.start()
        try:
            first.enqueue(_prepared(b"a"), "job-a")
            first.enqueue(_prepared(b"b"), "job-b")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and len(seen) < 2:
                time.sleep(0.02)
            self.assertEqual(sorted(seen), [b"a", b"b"])
            self.assertEqual(len(idents), 2, "both workers should claim a different job")
        finally:
            hold.set()
        self.assertTrue(first.wait_idle(5))
        for job_id in ("job-a", "job-b"):
            view = store.result(job_id)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(view.status, "succeeded", view.error)
            self.assertEqual(view.gcode, ";shared\n")
            self.assertEqual(view.headers["X-Profile-Id"], "apex-v2-insole-95a")
            again = store.claim()
            self.assertIsNone(again)


if __name__ == "__main__":
    unittest.main()
