"""Realistic synthetic microservice logs with injectable failure scenarios.

Topology (calls):
    api-gateway -> auth-service
    api-gateway -> orders-service -> inventory-service
                                  -> payments-service
                                  -> orders-db (postgres)
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

SCENARIOS = ("normal", "db_timeout", "http_500", "memory_pressure", "auth_failure", "cascading_failure", "mixed")

HOSTS = {
    "api-gateway": ["gw-1", "gw-2"],
    "auth-service": ["auth-1"],
    "orders-service": ["orders-1", "orders-2"],
    "inventory-service": ["inv-1"],
    "payments-service": ["pay-1"],
    "orders-db": ["pg-primary"],
}
ENDPOINTS = [("GET", "/api/orders"), ("POST", "/api/orders"), ("GET", "/api/orders/{id}"),
             ("GET", "/api/inventory/{sku}"), ("POST", "/api/checkout"), ("GET", "/api/profile")]
USERS = [f"user{n:04d}" for n in range(1, 400)]
ATTACKER_IPS = ["203.0.113.45", "203.0.113.46", "198.51.100.23"]


@dataclass
class Window:
    kind: str
    start: datetime
    end: datetime

    def active(self, t: datetime) -> bool:
        return self.start <= t < self.end

    def progress(self, t: datetime) -> float:
        return (t - self.start) / (self.end - self.start)


class LogGenerator:
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.logs: list[dict[str, Any]] = []

    # -------------------------------------------------------------- helpers
    def _emit(self, ts: datetime, service: str, severity: str, message: str, trace_id: str | None = None,
              **meta: Any) -> None:
        self.logs.append({
            "timestamp": ts.isoformat(timespec="milliseconds") + "Z",
            "severity": severity,
            "service": service,
            "host": self.rng.choice(HOSTS[service]),
            "trace_id": trace_id,
            "message": message,
            "metadata": {k: v for k, v in meta.items() if v is not None},
        })

    def _trace(self) -> str:
        return uuid.UUID(int=self.rng.getrandbits(128)).hex[:16]

    def _ip(self) -> str:
        return f"10.{self.rng.randint(0, 3)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"

    def _jitter(self, ts: datetime, lo_ms: int, hi_ms: int) -> datetime:
        return ts + timedelta(milliseconds=self.rng.randint(lo_ms, hi_ms))

    # -------------------------------------------------------------- request simulation
    def _request(self, ts: datetime, windows: list[Window]) -> None:
        r = self.rng
        trace = self._trace()
        method, path = r.choice(ENDPOINTS)
        path = path.replace("{id}", str(r.randint(1000, 99999))).replace("{sku}", f"SKU-{r.randint(100, 999)}")
        user = r.choice(USERS)
        active = {w.kind for w in windows if w.active(ts)}
        prog = {w.kind: w.progress(ts) for w in windows if w.active(ts)}

        # Auth step
        t = self._jitter(ts, 1, 5)
        if "auth_failure" in active and r.random() < 0.55:
            ip = r.choice(ATTACKER_IPS)
            self._emit(t, "auth-service", "WARNING",
                       f"Authentication failed for user={r.choice(USERS)}: invalid password from ip={ip}",
                       trace, client_ip=ip, reason="invalid_password")
            self._emit(self._jitter(t, 2, 6), "api-gateway", "WARNING",
                       f"{method} {path} -> 401 Unauthorized", trace, status=401, latency_ms=r.randint(8, 25),
                       client_ip=ip)
            if r.random() < 0.25:
                self._emit(self._jitter(t, 3, 8), "auth-service", "WARNING",
                           f"Account locked after repeated failures user={r.choice(USERS)}", trace)
            return
        if r.random() < 0.004:  # background noise: the occasional typo'd password
            self._emit(t, "auth-service", "WARNING", f"Authentication failed for user={user}: invalid password "
                       f"from ip={self._ip()}", trace, reason="invalid_password")
            self._emit(self._jitter(t, 2, 6), "api-gateway", "INFO", f"{method} {path} -> 401 Unauthorized",
                       trace, status=401, latency_ms=r.randint(8, 25))
            return
        self._emit(t, "auth-service", "DEBUG", f"Token validated for user={user} (scope=orders)", trace)

        if path == "/api/profile":
            lat = r.randint(15, 60)
            self._emit(self._jitter(ts, lat, lat + 3), "api-gateway", "INFO", f"{method} {path} -> 200 OK in {lat}ms",
                       trace, status=200, latency_ms=lat, user=user)
            return

        # Orders service
        t = self._jitter(ts, 5, 12)
        self._emit(t, "orders-service", "INFO", f"Handling {method} {path} for user={user}", trace)
        status, latency, error_msg = 200, r.randint(30, 140), None

        # Database call
        db_t = self._jitter(t, 2, 10)
        q_ms = int(r.lognormvariate(2.6, 0.5))
        if "db_timeout" in active and r.random() < 0.15 + 0.6 * prog["db_timeout"]:
            if r.random() < 0.3:
                self._emit(db_t, "orders-db", "WARNING", f"Connection pool saturated: 100/100 active connections, "
                           f"{r.randint(20, 80)} waiting", None, active_connections=100)
            self._emit(self._jitter(db_t, 4000, 5000), "orders-db", "ERROR",
                       f"canceling statement due to statement timeout: SELECT * FROM orders WHERE customer_id = "
                       f"{r.randint(1, 9999)}", trace, duration_ms=5000)
            self._emit(self._jitter(db_t, 5000, 5050), "orders-service", "ERROR",
                       "Database query timed out after 5000ms (pool=orders-primary)", trace, latency_ms=5000)
            status, latency, error_msg = 500, 5000 + r.randint(10, 200), "database timeout"
        else:
            self._emit(db_t, "orders-db", "DEBUG", f"Query executed in {q_ms}ms rows={r.randint(0, 50)}", trace,
                       duration_ms=q_ms)
            latency += q_ms
            if q_ms > 120 and r.random() < 0.3:
                self._emit(db_t, "orders-db", "WARNING", f"Slow query detected: {q_ms}ms on table orders", trace,
                           duration_ms=q_ms)

        # Inventory call
        if status == 200 and ("inventory" in path or method == "POST"):
            inv_t = self._jitter(t, 10, 30)
            mem = "memory_pressure" in active
            crashed = mem and prog["memory_pressure"] > 0.55 and prog["memory_pressure"] < 0.8
            if crashed:
                self._emit(inv_t, "orders-service", "ERROR",
                           "Request to inventory-service failed: dial tcp 10.0.4.12:8080: connection refused",
                           trace, latency_ms=r.randint(2, 15))
                if r.random() < 0.3:
                    self._emit(inv_t, "orders-service", "WARNING",
                               "Circuit breaker 'inventory' opened after 5 consecutive failures", trace)
                status, error_msg = 503, "inventory unavailable"
            else:
                inv_lat = r.randint(10, 60)
                if mem:
                    inv_lat = int(inv_lat * (1 + 25 * prog["memory_pressure"]))
                self._emit(self._jitter(inv_t, inv_lat, inv_lat + 2), "inventory-service", "INFO",
                           f"Stock check completed for SKU-{r.randint(100, 999)} in {inv_lat}ms", trace,
                           latency_ms=inv_lat)
                latency += inv_lat
                if inv_lat > 1500:
                    self._emit(inv_t, "orders-service", "WARNING",
                               f"Upstream request to inventory-service timed out after {inv_lat}ms", trace,
                               latency_ms=inv_lat)

        # Payments call
        if status == 200 and path in ("/api/checkout",) or (status == 200 and method == "POST" and r.random() < 0.5):
            pay_t = self._jitter(t, 20, 60)
            if "http_500" in active and r.random() < 0.6:
                self._emit(pay_t, "payments-service", "ERROR",
                           "Unhandled exception in PaymentProcessor.charge: NullPointerException: "
                           "currency is null (build 2024.10.3)", trace, exception="NullPointerException",
                           version="2024.10.3")
                self._emit(self._jitter(pay_t, 1, 3), "orders-service", "ERROR",
                           "payments-service responded with status=500 Internal Server Error", trace)
                status, error_msg = 500, "payment failure"
            else:
                p_lat = r.randint(40, 180)
                self._emit(self._jitter(pay_t, p_lat, p_lat + 2), "payments-service", "INFO",
                           f"Charge authorized amount={r.randint(5, 500)}.{r.randint(0, 99):02d} USD in {p_lat}ms",
                           trace, latency_ms=p_lat)
                latency += p_lat

        # Gateway response
        gw_t = ts + timedelta(milliseconds=latency)
        if status == 200 and r.random() < 0.002:  # sporadic background 5xx
            status, error_msg = 502, "transient upstream reset"
        if status >= 500:
            reason = {500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}[status]
            self._emit(gw_t, "api-gateway", "ERROR", f"{method} {path} -> {status} {reason} in {latency}ms",
                       trace, status=status, latency_ms=latency, user=user, upstream_error=error_msg)
        else:
            self._emit(gw_t, "api-gateway", "INFO", f"{method} {path} -> 200 OK in {latency}ms", trace,
                       status=200, latency_ms=latency, user=user)

    def _background(self, ts: datetime, windows: list[Window]) -> None:
        r = self.rng
        active = {w.kind: w for w in windows if w.active(ts)}
        if r.random() < 0.15:
            self._emit(ts, "inventory-service", "DEBUG", f"Cache refresh completed: {r.randint(800, 1200)} SKUs", None)
        if r.random() < 0.05:
            self._emit(ts, "orders-db", "INFO", f"checkpoint complete: wrote {r.randint(100, 900)} buffers", None)
        if r.random() < 0.08:
            heap = r.randint(45, 62)
            self._emit(ts, "inventory-service", "DEBUG", f"JVM heap usage {heap}% (GC young pause {r.randint(5, 20)}ms)",
                       None, heap_pct=heap)
        if "memory_pressure" in active:
            p = active["memory_pressure"].progress(ts)
            if p < 0.55 and r.random() < 0.5:
                heap = int(78 + 20 * p / 0.55)
                self._emit(ts, "inventory-service", "WARNING",
                           f"Memory pressure: heap usage at {heap}% (GC overhead {int(10 + 60 * p)}%)", None,
                           heap_pct=heap)
                if r.random() < 0.3:
                    self._emit(ts, "inventory-service", "WARNING", f"Long GC pause {r.randint(800, 3000)}ms "
                               f"(old generation)", None)
            elif 0.55 <= p < 0.58 and not getattr(self, "_oom_done", False):
                self._oom_done = True
                self._emit(ts, "inventory-service", "CRITICAL",
                           "java.lang.OutOfMemoryError: Java heap space at StockCache.load", None)
                self._emit(self._jitter(ts, 300, 600), "inventory-service", "CRITICAL",
                           "Container inventory-service OOMKilled (exit code 137), restarting", None, exit_code=137)
            elif 0.8 <= p < 0.83 and not getattr(self, "_restart_done", False):
                self._restart_done = True
                self._emit(ts, "inventory-service", "INFO", "inventory-service started on port 8080 (pid 1)", None)

    # -------------------------------------------------------------- public
    def generate(self, scenario: str = "mixed", minutes: int = 60, requests_per_minute: int = 40,
                 end_time: datetime | None = None) -> list[dict[str, Any]]:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")
        end = (end_time or datetime.now(timezone.utc)).replace(tzinfo=None)
        start = end - timedelta(minutes=minutes)
        windows = self._windows(scenario, start, minutes)
        self.logs = []
        self._oom_done = self._restart_done = False

        t = start
        mean_gap = 60.0 / requests_per_minute
        next_bg = start
        while t < end:
            # Inventory is down mid memory-scenario: fewer of its own logs.
            self._request(t, windows)
            while next_bg <= t:
                self._background(next_bg, windows)
                next_bg += timedelta(seconds=2)
            t += timedelta(seconds=self.rng.expovariate(1.0 / mean_gap))
        self._apply_outage_silence(windows)
        self.logs.sort(key=lambda l: l["timestamp"])
        return self.logs

    def _apply_outage_silence(self, windows: list[Window]) -> None:
        """While inventory-service is restarting it produces no logs at all."""
        for w in windows:
            if w.kind != "memory_pressure":
                continue
            span = w.end - w.start
            down_start, down_end = w.start + span * 0.57, w.start + span * 0.8
            self.logs = [l for l in self.logs if not (
                l["service"] == "inventory-service"
                and down_start <= datetime.fromisoformat(l["timestamp"][:-1]) < down_end
                and "OOMKilled" not in l["message"] and "OutOfMemoryError" not in l["message"])]

    @staticmethod
    def _windows(scenario: str, start: datetime, minutes: int) -> list[Window]:
        def w(kind: str, at: float, dur: float) -> Window:
            s = start + timedelta(minutes=minutes * at)
            return Window(kind, s, s + timedelta(minutes=max(2.0, minutes * dur)))

        if scenario == "normal":
            return []
        if scenario == "mixed":
            return [w("db_timeout", 0.22, 0.08), w("auth_failure", 0.45, 0.05),
                    w("memory_pressure", 0.62, 0.14), w("http_500", 0.86, 0.06)]
        if scenario == "cascading_failure":
            return [w("memory_pressure", 0.45, 0.25), w("db_timeout", 0.6, 0.1)]
        return [w(scenario, 0.6, 0.15 if scenario == "memory_pressure" else 0.1)]


def generate_logs(scenario: str = "mixed", minutes: int = 60, requests_per_minute: int = 40,
                  seed: int | None = None, end_time: datetime | None = None) -> list[dict[str, Any]]:
    return LogGenerator(seed).generate(scenario, minutes, requests_per_minute, end_time)
