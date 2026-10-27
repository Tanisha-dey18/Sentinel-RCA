"""Evidence-based root-cause analysis.

Output separates three kinds of statements:
  * facts      - things directly observed in logs (counts, first occurrences, traces)
  * signals    - statistical anomalies (robust z-scores against a baseline)
  * hypotheses - inferred root causes, ranked by a transparent weighted score

Every statement carries references to the log IDs / anomaly IDs / trace IDs it is based on.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import pandas as pd

from app.services.correlation import CorrelatedGroup, DependencyGraph
from app.services.detection import ERROR_SEVERITIES, SEVERITY_WEIGHT, DetectedAnomaly
from app.services.signatures import SignatureMatcher

# How likely a category is to be an origin (cause) rather than a symptom (effect).
CAUSE_WEIGHT: dict[str, float] = {
    "database": 1.0, "resource": 1.0, "storage": 1.0, "config": 0.9, "application": 0.85,
    "security": 0.8, "dependency": 0.6, "availability": 0.6, "traffic": 0.5, "network": 0.4,
    "resilience": 0.2, "http": 0.1,
}
STATISTICAL_ONLY_WEIGHT = 0.3

SCORE_WEIGHTS = {"precedence": 0.30, "propagation": 0.25, "dependency_depth": 0.15,
                 "cause_category": 0.15, "signal_strength": 0.15}

# (cause signature, effect signature, explanation)
CAUSAL_RULES: list[tuple[str, str, str]] = [
    ("db_pool_exhausted", "db_timeout", "An exhausted connection pool makes queries wait until they time out."),
    ("deadlock", "db_timeout", "Lock contention / deadlocks hold transactions until they time out."),
    ("disk_full", "db_timeout", "A full disk stalls database writes, causing query timeouts."),
    ("disk_full", "service_crash", "A full disk can crash processes that cannot write state or logs."),
    ("db_timeout", "http_5xx", "Database timeouts surface to callers as HTTP 5xx errors."),
    ("db_timeout", "upstream_timeout", "Slow database calls make upstream RPCs exceed their deadlines."),
    ("db_timeout", "circuit_breaker_open", "Repeated database failures trip circuit breakers."),
    ("db_pool_exhausted", "http_5xx", "Requests that cannot get a DB connection fail with 5xx."),
    ("memory_pressure", "out_of_memory", "Sustained memory pressure ends in an out-of-memory condition."),
    ("memory_pressure", "upstream_timeout", "GC pauses under memory pressure slow responses past deadlines."),
    ("out_of_memory", "service_crash", "The OOM killer / runtime terminates the process, forcing a restart."),
    ("service_crash", "connection_refused", "While the process restarts, callers get refused connections."),
    ("out_of_memory", "connection_refused", "An OOM-killed service stops accepting connections."),
    ("connection_refused", "http_5xx", "Callers that cannot reach a dependency return 5xx errors."),
    ("connection_refused", "circuit_breaker_open", "Unreachable dependencies trip circuit breakers."),
    ("upstream_timeout", "http_5xx", "Timed-out upstream calls are returned to clients as 5xx (often 504)."),
    ("unhandled_exception", "http_5xx", "Unhandled exceptions in request handlers produce HTTP 500 responses."),
    ("circuit_breaker_open", "http_5xx", "Requests short-circuited by an open breaker fail fast with 5xx."),
    ("tls_error", "connection_refused", "TLS handshake failures prevent connections to a dependency."),
    ("tls_error", "http_5xx", "TLS failures to a dependency surface as 5xx errors."),
    ("auth_failure", "rate_limited", "A burst of failed logins triggers rate limiting."),
]

KNOWLEDGE: dict[str, dict[str, Any]] = {
    "db_timeout": {
        "title": "Database query timeouts in {service}",
        "explanation": "Queries issued by {service} exceeded their deadline. Typical causes are slow queries "
                       "(missing index, lock contention), an overloaded database, or an exhausted connection pool.",
        "actions": ["Inspect slow-query logs and active locks on the database",
                    "Check DB CPU, IOPS and connection counts around the incident start",
                    "Review recent schema or query changes / deployments",
                    "Consider statement timeouts and connection-pool sizing"],
    },
    "db_pool_exhausted": {
        "title": "Connection pool exhaustion in {service}",
        "explanation": "{service} could not obtain database connections, so requests queued and failed.",
        "actions": ["Look for connection leaks (connections not returned to the pool)",
                    "Compare pool size with DB max_connections and replica count",
                    "Check for long-running transactions holding connections"],
    },
    "deadlock": {
        "title": "Database deadlocks / lock contention in {service}",
        "explanation": "Concurrent transactions in {service} blocked each other.",
        "actions": ["Inspect deadlock graphs in DB logs", "Make lock acquisition order consistent",
                    "Shorten transaction scope"],
    },
    "memory_pressure": {
        "title": "Memory exhaustion in {service}",
        "explanation": "{service} ran under sustained memory pressure, which degrades latency (GC) and can end "
                       "in out-of-memory kills.",
        "actions": ["Check heap / RSS metrics and recent traffic changes",
                    "Capture a heap dump to look for leaks", "Review container memory limits"],
    },
    "out_of_memory": {
        "title": "Out-of-memory failure in {service}",
        "explanation": "{service} exhausted its available memory and was terminated or failed allocations.",
        "actions": ["Inspect OOM-kill events and memory limits", "Look for memory leaks or unbounded caches",
                    "Scale out or raise limits as a mitigation"],
    },
    "service_crash": {
        "title": "Process crash / restart of {service}",
        "explanation": "{service} terminated unexpectedly, making it unavailable to its callers.",
        "actions": ["Check exit codes and crash logs", "Look for preceding OOM or panic messages",
                    "Verify liveness/readiness probe settings"],
    },
    "connection_refused": {
        "title": "{service} cannot reach a dependency",
        "explanation": "{service} saw refused or reset connections, meaning a dependency was down, restarting or "
                       "unreachable.",
        "actions": ["Check health of the target dependency", "Verify service discovery / DNS and network policies"],
    },
    "unhandled_exception": {
        "title": "Application exceptions in {service}",
        "explanation": "{service} raised unhandled exceptions — commonly a code defect introduced by a recent "
                       "deployment or unexpected input data.",
        "actions": ["Correlate the first exception with recent deployments", "Inspect the stack trace in evidence logs",
                    "Roll back if a release coincides with the incident start"],
    },
    "auth_failure": {
        "title": "Authentication failure burst on {service}",
        "explanation": "{service} rejected an unusual number of authentication attempts. If attempts come from few "
                       "sources this suggests credential stuffing / brute force; if from everywhere, an identity "
                       "provider or key/secret problem.",
        "actions": ["Group failures by source IP and user", "Check identity-provider health and secret rotation",
                    "Enable rate limiting / temporary blocks for offending sources"],
    },
    "disk_full": {
        "title": "Disk exhaustion on {service}",
        "explanation": "A volume used by {service} ran out of space.",
        "actions": ["Free space / expand the volume", "Check log rotation and temp-file cleanup"],
    },
    "tls_error": {
        "title": "TLS / certificate failure in {service}",
        "explanation": "TLS negotiation failed for {service}, often due to an expired or mismatched certificate.",
        "actions": ["Check certificate expiry dates", "Verify trust stores and SNI configuration"],
    },
    "upstream_timeout": {
        "title": "Upstream timeouts in {service}",
        "explanation": "Calls made by {service} to other services timed out.",
        "actions": ["Identify which dependency is slow", "Review timeout and retry budgets"],
    },
    "http_5xx": {
        "title": "HTTP 5xx errors from {service}",
        "explanation": "{service} returned server errors. On its own this is usually a symptom; the originating "
                       "failure is typically in {service} itself or a dependency it calls.",
        "actions": ["Inspect the failing endpoints and their dependencies", "Check recent deployments of {service}"],
    },
    "circuit_breaker_open": {
        "title": "Circuit breaker opened in {service}",
        "explanation": "{service} stopped calling a failing dependency. This is a protective reaction (symptom).",
        "actions": ["Find the dependency the breaker protects and check its health"],
    },
    "rate_limited": {
        "title": "Rate limiting in {service}",
        "explanation": "{service} throttled requests due to excessive traffic.",
        "actions": ["Identify the clients generating traffic", "Review rate-limit thresholds"],
    },
}

STAT_KNOWLEDGE = {
    "error_count": ("Error spike in {service}", "{service} logged significantly more errors than its baseline."),
    "latency_p95_ms": ("Latency degradation in {service}", "{service} responded much slower than its baseline."),
    "log_volume_drop": ("{service} went quiet", "{service} stopped producing its usual log volume, consistent "
                        "with a crash, hang or network partition."),
}


def _iso(d: datetime | pd.Timestamp | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, pd.Timestamp):
        d = d.to_pydatetime()
    return d.isoformat(timespec="milliseconds") + "Z"


def _hms(d: datetime | pd.Timestamp) -> str:
    """Human-readable UTC time for prose statements (data fields keep full ISO timestamps)."""
    return f"{d:%H:%M:%S} UTC"


def _confidence_label(c: float) -> str:
    return "high" if c >= 0.7 else "medium" if c >= 0.45 else "low"


def analyze_incident(group: CorrelatedGroup, logs: pd.DataFrame, graph: DependencyGraph,
                     matcher: SignatureMatcher) -> dict[str, Any]:
    """Produce root cause, timeline, title and summary for one correlated incident.

    `logs` must contain the incident's logs (id, timestamp, service, severity, message, trace_id, sigs).
    """
    anomalies = sorted(group.anomalies, key=lambda a: a.window_start)
    services = group.services
    logs = logs.sort_values("timestamp", kind="stable") if not logs.empty else logs
    problem = logs[logs["severity"].isin(ERROR_SEVERITIES) | logs["sigs"].map(bool)] if not logs.empty else logs
    if not problem.empty:
        # Keep only problem logs that fall inside an anomaly window of their own service.
        keep = pd.Series(False, index=problem.index)
        for a in anomalies:
            keep |= ((problem["service"] == a.service) & (problem["timestamp"] >= a.window_start)
                     & (problem["timestamp"] < a.window_end))
        problem = problem[keep]

    facts: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []

    def add_fact(statement: str, **evidence: Any) -> str:
        fid = f"F{len(facts) + 1}"
        facts.append({"id": fid, "statement": statement, "evidence": {k: v for k, v in evidence.items() if v}})
        return fid

    # ------------------------------------------------------------------ facts: signatures
    sig_stats: dict[str, dict[str, Any]] = {}
    if not problem.empty:
        ex = problem[["id", "timestamp", "service", "sigs", "trace_id"]].explode("sigs").dropna(subset=["sigs"])
        # Only signatures that crossed their threshold for a service in this incident count as facts.
        fired = {(a.signature_key, a.service) for a in anomalies if a.signature_key}
        ex = ex[[(k, s) in fired for k, s in zip(ex["sigs"], ex["service"])]]
        for key, g in ex.groupby("sigs", sort=False):
            sig = matcher.get(key)
            first = g.iloc[0]
            per_service = g.groupby("service")["timestamp"].min().sort_values()
            sig_stats[key] = {
                "key": key, "name": sig.name if sig else key, "category": sig.category if sig else None,
                "count": int(len(g)), "first_seen": first["timestamp"], "last_seen": g["timestamp"].max(),
                "first_log_id": int(first["id"]), "services": list(per_service.index),
                "first_seen_by_service": {s: t for s, t in per_service.items()},
                "sample_log_ids": [int(i) for i in g["id"].head(5)],
                "trace_ids": set(g["trace_id"].dropna()),
            }
    for st in sorted(sig_stats.values(), key=lambda s: s["first_seen"]):
        st["fact_id"] = add_fact(
            f"{st['count']} log(s) matched '{st['name']}' in {', '.join(st['services'])}; first at "
            f"{_hms(st['first_seen'])} ({st['services'][0]}).",
            log_ids=st["sample_log_ids"],
        )

    # ------------------------------------------------------------------ facts: services
    affected: list[dict[str, Any]] = []
    for s in services:
        s_anoms = [a for a in anomalies if a.service == s]
        s_err = problem[problem["service"] == s] if not problem.empty else problem
        # Onset = first problem log inside one of this service's own anomaly windows, so unrelated
        # background noise just before the incident cannot make a service look like the origin.
        if not s_err.empty:
            in_window = pd.Series(False, index=s_err.index)
            for a in s_anoms:
                in_window |= (s_err["timestamp"] >= a.window_start) & (s_err["timestamp"] < a.window_end)
            s_err = s_err[in_window]
        first_err = s_err.iloc[0] if not s_err.empty else None
        first_seen = (first_err["timestamp"].to_pydatetime() if first_err is not None
                      else min(a.window_start for a in s_anoms))
        entry = {
            "service": s,
            "first_seen": _iso(first_seen),
            "first_error_log_id": int(first_err["id"]) if first_err is not None else None,
            "first_error_message": str(first_err["message"])[:300] if first_err is not None else None,
            "error_logs": int((s_err["severity"].isin(ERROR_SEVERITIES)).sum()) if not s_err.empty else 0,
            "signatures": sorted({a.signature_key for a in s_anoms if a.signature_key}),
            "anomaly_ids": [a.id for a in s_anoms if a.id is not None],
            "_first_seen_dt": first_seen,
        }
        affected.append(entry)
    affected.sort(key=lambda e: e["_first_seen_dt"])
    first_entry = next((e for e in affected if e["first_error_log_id"] is not None), None)
    if first_entry:
        add_fact(f"Earliest problem log inside an anomaly window: {first_entry['service']} at "
                 f"{_hms(first_entry['_first_seen_dt'])}: \"{(first_entry['first_error_message'] or '')[:160]}\"",
                 log_ids=[first_entry["first_error_log_id"]])

    # ------------------------------------------------------------------ facts: cross-service traces
    propagation_traces: list[dict[str, Any]] = []
    origin_counts: Counter[str] = Counter()
    if not problem.empty:
        traced = problem.dropna(subset=["trace_id"])
        for tid, g in traced.groupby("trace_id", sort=False):
            path = list(dict.fromkeys(g["service"]))  # services in order of first problem log
            if len(path) < 2:
                continue
            origin_counts[path[0]] += 1
            if len(propagation_traces) < 15:
                propagation_traces.append({"trace_id": str(tid), "path": path, "first_log_id": int(g.iloc[0]["id"]),
                                           "log_ids": [int(i) for i in g["id"].head(10)]})
    total_cross = sum(origin_counts.values())
    if total_cross:
        top_origin, n = origin_counts.most_common(1)[0]
        add_fact(f"{total_cross} trace(s) contain problem logs in 2+ services; in {n} of them the first failing "
                 f"service was {top_origin}.",
                 trace_ids=[t["trace_id"] for t in propagation_traces[:10]],
                 log_ids=[t["first_log_id"] for t in propagation_traces[:10]])

    # ------------------------------------------------------------------ signals
    for a in anomalies:
        if a.kind != "statistical":
            continue
        signals.append({
            "id": f"S{len(signals) + 1}", "anomaly_id": a.id, "service": a.service, "metric": a.metric,
            "robust_z": a.robust_z, "observed": a.observed_value, "baseline_median": a.baseline_median,
            "mad": a.mad, "window_start": _iso(a.window_start), "window_end": _iso(a.window_end),
            "statement": a.description, "log_ids": a.evidence_log_ids[:10],
        })

    # ------------------------------------------------------------------ causal chain (rule-based inference)
    chain: list[dict[str, Any]] = []
    for cause, effect, why in CAUSAL_RULES:
        c, e = sig_stats.get(cause), sig_stats.get(effect)
        if not c or not e or c["first_seen"] > e["first_seen"]:
            continue
        lag = (e["first_seen"] - c["first_seen"]).total_seconds()
        shared = sorted(c["trace_ids"] & e["trace_ids"])
        chain.append({
            "cause": cause, "effect": effect, "cause_name": c["name"], "effect_name": e["name"],
            "cause_services": c["services"], "effect_services": e["services"],
            "lag_seconds": round(lag, 1), "trace_linked": bool(shared), "shared_trace_ids": shared[:5],
            "explanation": why,
            "evidence": {"cause_log_ids": c["sample_log_ids"][:3], "effect_log_ids": e["sample_log_ids"][:3],
                         "fact_ids": [c["fact_id"], e["fact_id"]]},
        })

    # ------------------------------------------------------------------ hypotheses
    first_times = {e["service"]: e["_first_seen_dt"] for e in affected}
    ordered_times = sorted(set(first_times.values()))
    n_services = len(services)
    hypotheses: list[dict[str, Any]] = []
    effect_keys = {c["effect"] for c in chain}
    for s in services:
        s_anoms = [a for a in anomalies if a.service == s]
        rank = ordered_times.index(first_times[s])
        precedence = 1.0 if len(ordered_times) == 1 else 1.0 - rank / (len(ordered_times) - 1)
        propagation = origin_counts[s] / total_cross if total_cross else (1.0 if n_services == 1 else 0.0)
        upstream_affected = graph.upstream_of(s) & set(services)
        depth = len(upstream_affected) / (n_services - 1) if n_services > 1 else 1.0
        sig_anoms = [a for a in s_anoms if a.signature_key]
        if sig_anoms:
            # Prefer signatures that are causes in the observed chain over pure symptoms.
            def _cause_rank(a: DetectedAnomaly) -> tuple[float, float]:
                w = CAUSE_WEIGHT.get(a.category or "", 0.3)
                if a.signature_key in effect_keys and not any(c["cause"] == a.signature_key for c in chain):
                    w *= 0.6
                return (w, -a.window_start.timestamp())
            dominant = max(sig_anoms, key=_cause_rank)
            cause_cat = CAUSE_WEIGHT.get(dominant.category or "", 0.3)
        else:
            dominant = max(s_anoms, key=lambda a: a.score)
            cause_cat = STATISTICAL_ONLY_WEIGHT
        strength = max((max(a.score, SEVERITY_WEIGHT.get(a.severity, 0.5)) for a in s_anoms), default=0.0)
        factors = {"precedence": round(precedence, 3), "propagation": round(propagation, 3),
                   "dependency_depth": round(depth, 3), "cause_category": round(cause_cat, 3),
                   "signal_strength": round(strength, 3)}
        score = sum(SCORE_WEIGHTS[k] * v for k, v in factors.items())

        if dominant.signature_key and dominant.signature_key in KNOWLEDGE:
            kb = KNOWLEDGE[dominant.signature_key]
            title = kb["title"].format(service=s)
            explanation = kb["explanation"].format(service=s)
            actions = [a.format(service=s) for a in kb["actions"]]
        else:
            t, ex = STAT_KNOWLEDGE.get(dominant.metric, ("Anomalous behaviour in {service}", "{service} deviated "
                                                                                              "from baseline."))
            title, explanation, actions = t.format(service=s), ex.format(service=s), [
                f"Inspect {s} logs and metrics around the anomaly window", "Check recent deployments and config changes"]

        reasoning = []
        reasoning.append(f"{s} was the {'first' if rank == 0 else f'#{rank + 1}'} of {len(ordered_times)} "
                         f"distinct onset time(s) among affected services (onset {_hms(first_times[s])}).")
        if total_cross:
            reasoning.append(f"{s} was the first failing service in {origin_counts[s]}/{total_cross} cross-service "
                             f"error traces.")
        if upstream_affected:
            reasoning.append(f"Affected services that call {s} (directly or transitively): "
                             f"{', '.join(sorted(upstream_affected))} — failures can propagate up to them.")
        if dominant.signature_key:
            reasoning.append(f"Dominant signature '{dominant.signature_key}' (category {dominant.category}) "
                             f"is typically a {'cause' if cause_cat >= 0.6 else 'symptom'}.")
        else:
            reasoning.append(f"Only statistical signals ({dominant.metric}) — no specific failure signature matched.")
        related_chain = [c for c in chain if s in c["cause_services"]]
        for c in related_chain[:3]:
            reasoning.append(f"Causal rule: {c['cause_name']} → {c['effect_name']} (+{c['lag_seconds']:.0f}s"
                             f"{', linked by shared traces' if c['trace_linked'] else ''}). {c['explanation']}")

        ev_logs = []
        for a in sorted(s_anoms, key=lambda a: -a.score)[:3]:
            ev_logs.extend(a.evidence_log_ids[:4])
        hypotheses.append({
            "service": s, "signature_key": dominant.signature_key, "category": dominant.category,
            "title": title, "explanation": explanation, "score": round(score, 3), "factors": factors,
            "reasoning": reasoning, "recommendations": actions,
            "evidence": {"anomaly_ids": [a.id for a in s_anoms if a.id is not None],
                         "log_ids": list(dict.fromkeys(ev_logs))[:12],
                         "trace_ids": [t["trace_id"] for t in propagation_traces if t["path"][0] == s][:5]},
        })

    hypotheses.sort(key=lambda h: -h["score"])
    for i, h in enumerate(hypotheses):
        h["rank"] = i + 1
        margin = h["score"] - (hypotheses[i + 1]["score"] if i + 1 < len(hypotheses) else 0.0)
        # Confidence: the score itself, discounted when the runner-up is close (ambiguity).
        conf = h["score"] * (0.75 + 0.25 * min(1.0, margin / 0.15)) if len(hypotheses) > 1 else h["score"] * 0.9
        if any(c["trace_linked"] for c in chain if h["service"] in c["cause_services"]):
            conf = min(1.0, conf + 0.05)
        h["confidence"] = round(conf, 3)
        h["confidence_label"] = _confidence_label(conf)

    primary = hypotheses[0] if hypotheses else None

    # ------------------------------------------------------------------ timeline
    timeline: list[dict[str, Any]] = []
    for st in sig_stats.values():
        for svc, t in st["first_seen_by_service"].items():
            timeline.append({"timestamp": _iso(t), "type": "fact", "kind": "first_occurrence", "service": svc,
                             "title": f"First '{st['name']}' in {svc}",
                             "detail": f"{st['count']} matching log(s) across the incident.",
                             "signature_key": st["key"], "log_ids": [st["first_log_id"]] if svc == st["services"][0]
                             else st["sample_log_ids"][:1]})
    for a in anomalies:
        if a.kind == "statistical":
            timeline.append({"timestamp": _iso(a.window_start), "type": "signal", "kind": a.metric,
                             "service": a.service, "title": STAT_KNOWLEDGE.get(a.metric, ("Anomaly", ""))[0]
                             .format(service=a.service), "detail": a.description, "anomaly_id": a.id,
                             "robust_z": a.robust_z, "log_ids": a.evidence_log_ids[:3]})
    for c in chain:
        effect_first = sig_stats[c["effect"]]["first_seen"]
        timeline.append({"timestamp": _iso(effect_first), "type": "inference", "kind": "causal_link",
                         "service": c["effect_services"][0],
                         "title": f"{c['cause_name']} → {c['effect_name']}",
                         "detail": f"{c['explanation']} Lag {c['lag_seconds']:.0f}s"
                                   f"{'; linked by shared trace IDs' if c['trace_linked'] else ''}.",
                         "log_ids": c["evidence"]["effect_log_ids"][:2]})
    if not problem.empty:
        per_min = problem.set_index("timestamp").resample("1min").size()
        if not per_min.empty and per_min.max() > 0:
            peak_t = per_min.idxmax()
            timeline.append({"timestamp": _iso(peak_t), "type": "fact", "kind": "peak",
                             "service": None, "title": "Peak of problem logs",
                             "detail": f"{int(per_min.max())} problem log(s) in the busiest minute.", "log_ids": []})
    timeline.sort(key=lambda e: e["timestamp"] or "")
    timeline.insert(0, {"timestamp": _iso(group.start), "type": "fact", "kind": "incident_start", "service": None,
                        "title": "Incident window opens", "detail": "Start of the earliest correlated anomaly window.",
                        "log_ids": []})
    timeline.append({"timestamp": _iso(group.end), "type": "fact", "kind": "incident_end", "service": None,
                     "title": "Incident window closes", "detail": "End of the latest correlated anomaly window.",
                     "log_ids": []})

    # ------------------------------------------------------------------ title & summary
    if primary:
        title = primary["title"]
        others = [s for s in services if s != primary["service"]]
        if others:
            title += f" impacting {', '.join(others[:3])}{'…' if len(others) > 3 else ''}"
        summary = (f"{len(anomalies)} anomalies across {n_services} service(s) between {_hms(group.start)} and "
                   f"{_hms(group.end)}. Most probable root cause (inferred, {primary['confidence_label']} confidence "
                   f"{primary['confidence']:.0%}): {primary['title'].lower()}.")
    else:
        title, summary = "Anomalous activity", f"{len(anomalies)} anomalies detected."

    for e in affected:
        e.pop("_first_seen_dt", None)
    for st in sig_stats.values():
        st.pop("trace_ids", None)

    root_cause = {
        "primary": primary,
        "hypotheses": hypotheses,
        "facts": facts,
        "signals": signals,
        "causal_chain": chain,
        "affected_services": affected,
        "propagation_traces": propagation_traces,
        "signatures": [{k: (_iso(v) if isinstance(v, (datetime, pd.Timestamp)) else v)
                        for k, v in st.items() if k != "first_seen_by_service"} for st in sig_stats.values()],
        "correlation_links": Counter(l["reason"] for l in group.links),
        "dependency_graph": {"edges": [e for e in graph.to_dict()["edges"]
                                       if e["source"] in services or e["target"] in services]},
        "method": {
            "score_weights": SCORE_WEIGHTS,
            "description": "Hypotheses are ranked by a weighted sum of: temporal precedence (who failed first), "
                           "trace propagation (who failed first inside shared traces), dependency depth (how many "
                           "affected services call it), cause-category prior (database/resource failures are usually "
                           "origins, HTTP 5xx usually symptoms) and statistical signal strength.",
        },
    }
    return {
        "title": title[:500],
        "summary": summary,
        "confidence": primary["confidence"] if primary else 0.0,
        "categories": sorted({a.category for a in anomalies if a.category}),
        "root_cause": _jsonable(root_cause),
        "timeline": _jsonable(timeline),
    }


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, pd.Timestamp)):
        return _iso(obj)
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            return str(obj)
    return obj


def group_from_anomalies(anomalies: list[DetectedAnomaly]) -> CorrelatedGroup:
    return CorrelatedGroup(anomalies=anomalies, links=[])


def timeline_by_type(timeline: list[dict]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for e in timeline:
        out[e["type"]] += 1
    return dict(out)
