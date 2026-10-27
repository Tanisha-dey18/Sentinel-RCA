"""Redis-backed cache, correlation state and log stream, with an in-process fallback."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)
PREFIX = "sentinel:"


class Cache:
    def __init__(self) -> None:
        self._redis = None
        self._mem: dict[str, tuple[float | None, str]] = {}
        self._lock = threading.Lock()
        url = get_settings().redis_url
        if url:
            try:
                import redis

                # protocol=2 (RESP2): works with every Redis version; newer clients default to RESP3,
                # which Redis < 6 and some Redis-compatible servers reject.
                client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2,
                                              socket_connect_timeout=2, protocol=2)
                client.ping()
                self._redis = client
            except Exception as exc:
                log.warning("Redis unavailable at %s (%s); using in-process cache", url, exc)

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def get_json(self, key: str) -> Any:
        k = PREFIX + key
        if self._redis is not None:
            try:
                raw = self._redis.get(k)
                return json.loads(raw) if raw else None
            except Exception as exc:
                log.debug("redis get failed: %s", exc)
                return None
        with self._lock:
            item = self._mem.get(k)
            if not item:
                return None
            exp, raw = item
            if exp is not None and exp < time.time():
                self._mem.pop(k, None)
                return None
            return json.loads(raw)

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        k, raw = PREFIX + key, json.dumps(value, default=str)
        if self._redis is not None:
            try:
                self._redis.set(k, raw, ex=ttl)
                return
            except Exception as exc:
                log.debug("redis set failed: %s", exc)
                return
        with self._lock:
            self._mem[k] = (time.time() + ttl if ttl else None, raw)

    def delete_prefix(self, prefix: str) -> None:
        p = PREFIX + prefix
        if self._redis is not None:
            try:
                keys = list(self._redis.scan_iter(match=p + "*", count=500))
                if keys:
                    self._redis.delete(*keys)
            except Exception as exc:
                log.debug("redis delete failed: %s", exc)
            return
        with self._lock:
            for k in [k for k in self._mem if k.startswith(p)]:
                self._mem.pop(k, None)

    def append_stream(self, events: list[dict], maxlen: int = 10_000) -> None:
        """Mirror ingested logs to a Redis Stream so external consumers can tail them."""
        if self._redis is None or not events:
            return
        try:
            pipe = self._redis.pipeline()
            for e in events:
                pipe.xadd(PREFIX + "logs", {"data": json.dumps(e, default=str)}, maxlen=maxlen, approximate=True)
            pipe.execute()
        except Exception as exc:
            log.debug("redis xadd failed: %s", exc)

    def ping(self) -> bool:
        if self._redis is None:
            return True
        try:
            return bool(self._redis.ping())
        except Exception:
            return False


class Broadcaster:
    """Thread-safe fan-out of events to asyncio subscribers (used by the SSE live-tail endpoint)."""

    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, s) for l, s in self._subs if s is not q]

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put_nowait, q, event)
            except RuntimeError:  # loop closed
                self.unsubscribe(q)

    @property
    def subscribers(self) -> int:
        return len(self._subs)


def _put_nowait(q: asyncio.Queue, event: dict) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(event)


_cache: Cache | None = None
broadcaster = Broadcaster()


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
