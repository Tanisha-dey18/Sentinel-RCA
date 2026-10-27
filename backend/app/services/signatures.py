"""Configurable regex log signatures and a fast matcher."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Signature

DEFAULT_SIGNATURES: list[dict] = [
    {
        "key": "db_timeout", "name": "Database timeout", "category": "database", "severity": "high", "min_count": 2,
        "pattern": r"(database|\bdb\b|postgres|mysql|\bsql\b|query|connection pool).{0,80}(timeout|timed out)"
                   r"|(timeout|timed out).{0,80}(database|\bdb\b|query|postgres|mysql)",
        "description": "Queries or connections to a database exceeded their deadline.",
    },
    {
        "key": "db_pool_exhausted", "name": "Connection pool exhausted", "category": "database", "severity": "high",
        "min_count": 1,
        "pattern": r"(connection )?pool (exhausted|is full|saturated)|too many (db |database )?connections"
                   r"|could not (obtain|acquire) (a )?connection",
        "description": "The application could not obtain a database connection from its pool.",
    },
    {
        "key": "deadlock", "name": "Database deadlock", "category": "database", "severity": "high", "min_count": 1,
        "pattern": r"deadlock (detected|found)|lock wait timeout",
        "description": "Transactions blocked each other and were aborted.",
    },
    {
        "key": "http_5xx", "name": "HTTP 5xx response", "category": "http", "severity": "medium", "min_count": 3,
        "pattern": r"\b(status|http|code|response)[=: ]{0,3}5\d\d\b|\b5\d\d (internal server error|bad gateway"
                   r"|service unavailable|gateway timeout)|internal server error",
        "description": "Server-side HTTP failures returned to callers.",
    },
    {
        "key": "upstream_timeout", "name": "Upstream / RPC timeout", "category": "network", "severity": "medium",
        "min_count": 2,
        "pattern": r"(upstream|downstream|rpc|grpc|request to [\w.-]+).{0,60}(timeout|timed out|deadline exceeded)"
                   r"|deadline exceeded|gateway timeout",
        "description": "Calls to another service did not complete in time.",
    },
    {
        "key": "connection_refused", "name": "Dependency unreachable", "category": "dependency", "severity": "high",
        "min_count": 2,
        "pattern": r"connection refused|econnrefused|connection reset|no route to host|host unreachable"
                   r"|name or service not known|dial tcp .{0,60} (refused|unreachable)",
        "description": "A dependency refused or dropped network connections.",
    },
    {
        "key": "memory_pressure", "name": "Memory pressure", "category": "resource", "severity": "medium",
        "min_count": 2,
        "pattern": r"memory (pressure|usage (is )?(high|critical))|heap (usage|utili[sz]ation) (is )?(at |above )?(8\d|9\d|100)%"
                   r"|gc overhead limit|long gc pause|high memory usage",
        "description": "A process is approaching its memory limit.",
    },
    {
        "key": "out_of_memory", "name": "Out of memory", "category": "resource", "severity": "critical",
        "min_count": 1,
        "pattern": r"out ?of ?memory|outofmemoryerror|\boom(killed|-kill(er)?)?\b|cannot allocate memory"
                   r"|memoryerror|killed process \d+",
        "description": "A process exhausted available memory.",
    },
    {
        "key": "service_crash", "name": "Process crash / restart", "category": "availability", "severity": "high",
        "min_count": 1,
        "pattern": r"(container|pod|process|service|worker) .{0,40}(restarted|restarting|crashed|exited unexpectedly)"
                   r"|crashloopbackoff|segmentation fault|core dumped|^panic:",
        "description": "A process terminated unexpectedly or was restarted.",
    },
    {
        "key": "unhandled_exception", "name": "Unhandled exception", "category": "application", "severity": "medium",
        "min_count": 3,
        "pattern": r"unhandled exception|uncaught exception|nullpointerexception|traceback \(most recent call last\)"
                   r"|typeerror:|keyerror:|attributeerror:|illegalstateexception",
        "description": "Application code raised an exception that was not handled.",
    },
    {
        "key": "auth_failure", "name": "Authentication failure", "category": "security", "severity": "medium",
        "min_count": 8,
        "pattern": r"(authentication|auth|login|sign-?in) (failed|failure|denied|rejected)|invalid (credentials|password"
                   r"|api key|token)|\b401 unauthorized\b|account locked",
        "description": "Requests failed authentication; bursts may indicate credential stuffing or an IdP outage.",
    },
    {
        "key": "rate_limited", "name": "Rate limited", "category": "traffic", "severity": "low", "min_count": 5,
        "pattern": r"rate[ -]limit(ed| exceeded| reached)|too many requests|status[=: ]{0,3}429\b",
        "description": "Requests were throttled.",
    },
    {
        "key": "disk_full", "name": "Disk full", "category": "storage", "severity": "critical", "min_count": 1,
        "pattern": r"no space left on device|disk (is )?full|enospc|disk usage (above|at) 9\d%",
        "description": "A volume ran out of space.",
    },
    {
        "key": "circuit_breaker_open", "name": "Circuit breaker open", "category": "resilience", "severity": "medium",
        "min_count": 1,
        "pattern": r"circuit[ -]?breaker .{0,30}(open|opened|tripped)",
        "description": "A circuit breaker stopped calls to a failing dependency.",
    },
    {
        "key": "tls_error", "name": "TLS / certificate error", "category": "config", "severity": "high",
        "min_count": 1,
        "pattern": r"certificate (has )?(expired|verify failed|is not valid)|x509:|tls handshake (error|failed|timeout)"
                   r"|ssl(error|_error| error)",
        "description": "TLS negotiation failed, often due to an expired or misconfigured certificate.",
    },
]


@dataclass(frozen=True)
class SignatureDef:
    key: str
    name: str
    category: str
    pattern: str
    severity: str
    min_count: int
    description: str = ""


class SignatureMatcher:
    """Compiles signatures once and matches messages against all of them."""

    def __init__(self, signatures: list[SignatureDef]):
        self.signatures = {s.key: s for s in signatures}
        self._compiled: list[tuple[str, re.Pattern[str]]] = []
        for s in signatures:
            try:
                self._compiled.append((s.key, re.compile(s.pattern, re.IGNORECASE | re.MULTILINE)))
            except re.error:
                continue

    def match(self, message: str) -> list[str]:
        return [key for key, rx in self._compiled if rx.search(message)]

    def get(self, key: str) -> SignatureDef | None:
        return self.signatures.get(key)


def to_def(s: Signature) -> SignatureDef:
    return SignatureDef(key=s.key, name=s.name, category=s.category, pattern=s.pattern,
                        severity=s.severity, min_count=s.min_count, description=s.description)


def seed_default_signatures(db: Session) -> int:
    existing = set(db.scalars(select(Signature.key)))
    added = 0
    for d in DEFAULT_SIGNATURES:
        if d["key"] not in existing:
            db.add(Signature(**d, builtin=True, enabled=True))
            added += 1
    if added:
        db.commit()
    return added


def load_matcher(db: Session) -> SignatureMatcher:
    rows = db.scalars(select(Signature).where(Signature.enabled.is_(True))).all()
    return SignatureMatcher([to_def(r) for r in rows])


def default_matcher() -> SignatureMatcher:
    return SignatureMatcher([SignatureDef(**d) for d in DEFAULT_SIGNATURES])
