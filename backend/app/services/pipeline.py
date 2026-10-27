"""End-to-end analysis: load logs -> detect -> correlate -> RCA -> persist -> index."""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AnalysisRun, Anomaly, Incident, LogEntry, utcnow
from app.services.cache import get_cache
from app.services.correlation import CorrelatedGroup, DependencyGraph, correlate
from app.services.detection import DetectedAnomaly, detect_signatures, detect_statistical, prepare_frame
from app.services.narrative import generate_narrative
from app.services.rca import analyze_incident
from app.services.signatures import SignatureMatcher, load_matcher
from app.services.vector_store import get_vector_store

log = logging.getLogger(__name__)


def _load_rows(db: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    q = (select(LogEntry.id, LogEntry.timestamp, LogEntry.service, LogEntry.severity, LogEntry.message,
                LogEntry.trace_id, LogEntry.metadata_)
         .where(LogEntry.timestamp >= start, LogEntry.timestamp <= end)
         .order_by(LogEntry.timestamp))
    return [{"id": r[0], "timestamp": r[1], "service": r[2], "severity": r[3], "message": r[4],
             "trace_id": r[5], "metadata": r[6]} for r in db.execute(q)]


def _naive_utc(d: datetime | None) -> datetime | None:
    if d is None or d.tzinfo is None:
        return d
    return d.astimezone(timezone.utc).replace(tzinfo=None)


def _incident_fingerprint(group: CorrelatedGroup, window_seconds: int) -> str:
    start = group.start.replace(second=0, microsecond=0)
    raw = f"{start.isoformat()}|{','.join(group.services)}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _match_previous(group: CorrelatedGroup, candidates: list[Incident], taken: set[int]) -> Incident | None:
    """Find the previously stored incident that a re-detected group most likely is.

    Exact fingerprints break when a new baseline adds/removes a peripheral service or shifts the
    start by a window, so fall back to: overlapping time windows AND service-set Jaccard >= 0.5.
    """
    best, best_score = None, 0.0
    services = set(group.services)
    for inc in candidates:
        if inc.id in taken:
            continue
        overlap = (min(group.end, inc.ended_at) - max(group.start, inc.started_at)).total_seconds()
        if overlap <= 0:
            continue
        old = set(inc.services or [])
        jaccard = len(services & old) / len(services | old) if services | old else 0.0
        span = max((max(group.end, inc.ended_at) - min(group.start, inc.started_at)).total_seconds(), 1.0)
        score = jaccard + overlap / span
        if jaccard >= 0.5 and score > best_score:
            best, best_score = inc, score
    return best


def _to_model(a: DetectedAnomaly, run_id: int) -> Anomaly:
    return Anomaly(kind=a.kind, detector=a.detector, metric=a.metric, service=a.service,
                   signature_key=a.signature_key, category=a.category, window_start=a.window_start,
                   window_end=a.window_end, observed_value=a.observed_value, baseline_median=a.baseline_median,
                   mad=a.mad, robust_z=a.robust_z, score=a.score, severity=a.severity, description=a.description,
                   evidence_log_ids=a.evidence_log_ids, evidence_count=a.evidence_count, trace_ids=a.trace_ids,
                   run_id=run_id)


def incident_dict(inc: Incident, sample_logs: list[dict] | None = None) -> dict[str, Any]:
    return {"id": inc.id, "title": inc.title, "severity": inc.severity, "status": inc.status,
            "services": inc.services, "categories": inc.categories, "root_cause": inc.root_cause,
            "started_at": inc.started_at, "ended_at": inc.ended_at, "sample_logs": sample_logs or []}


def sample_logs_for(db: Session, inc: Incident, limit: int = 15) -> list[dict]:
    rc = inc.root_cause or {}
    ids: list[int] = []
    for f in rc.get("facts", []):
        ids += f.get("evidence", {}).get("log_ids", [])
    if rc.get("primary"):
        ids += rc["primary"].get("evidence", {}).get("log_ids", [])
    ids = list(dict.fromkeys(ids))[:limit]
    if not ids:
        return []
    rows = db.scalars(select(LogEntry).where(LogEntry.id.in_(ids)).order_by(LogEntry.timestamp)).all()
    return [{"log_id": r.id, "ts": r.timestamp.isoformat(), "severity": r.severity, "service": r.service,
             "message": r.message[:300], "trace_id": r.trace_id} for r in rows]


def run_analysis(db: Session, start: datetime | None = None, end: datetime | None = None,
                 window_seconds: int | None = None, z_threshold: float | None = None,
                 min_anomaly_count: int | None = None, correlation_gap_seconds: int | None = None,
                 use_llm: bool | None = None, matcher: SignatureMatcher | None = None) -> dict[str, Any]:
    s = get_settings()
    t0 = time.perf_counter()
    window_seconds = window_seconds or s.window_seconds
    z_threshold = z_threshold or s.z_threshold
    min_anomaly_count = min_anomaly_count or s.min_anomaly_count
    gap = s.correlation_gap_seconds if correlation_gap_seconds is None else correlation_gap_seconds
    use_llm = s.auto_llm_narrative if use_llm is None else use_llm

    start, end = _naive_utc(start), _naive_utc(end)
    if end is None:
        end = db.scalar(select(func.max(LogEntry.timestamp))) or utcnow()
    if start is None:
        earliest = db.scalar(select(func.min(LogEntry.timestamp))) or end
        start = max(earliest, end - timedelta(hours=s.analysis_lookback_hours))

    params = {"window_seconds": window_seconds, "z_threshold": z_threshold, "min_anomaly_count": min_anomaly_count,
              "correlation_gap_seconds": gap, "use_llm": use_llm}
    run = AnalysisRun(range_start=start, range_end=end, params=params, status="running")
    db.add(run)
    db.commit()

    try:
        matcher = matcher or load_matcher(db)
        df = prepare_frame(_load_rows(db, start, end), matcher)

        detected: list[DetectedAnomaly] = []
        if not df.empty:
            detected += detect_statistical(df, window_seconds, z_threshold, min_anomaly_count,
                                           s.min_baseline_windows, s.max_evidence_per_anomaly)
            detected += detect_signatures(df, matcher, window_seconds, z_threshold, s.max_evidence_per_anomaly)
        graph = DependencyGraph.from_frame(df)

        # Preserve human-managed state (status, notes) of incidents that are re-detected.
        old_incidents = db.scalars(select(Incident).where(Incident.ended_at >= start,
                                                          Incident.started_at <= end)).all()
        preserved = {i.fingerprint: i for i in old_incidents}
        db.execute(delete(Anomaly).where(Anomaly.window_start >= start, Anomaly.window_start <= end))
        db.flush()

        models = [_to_model(a, run.id) for a in detected]
        db.add_all(models)
        db.flush()
        for a, m in zip(detected, models):
            a.id = m.id
        model_by_id = {m.id: m for m in models}

        groups, _ = correlate(detected, graph, gap)
        kept_ids: set[int] = set()
        store = get_vector_store()
        incidents: list[Incident] = []

        for g in groups:
            g_start = g.start - timedelta(seconds=window_seconds)
            mask = (df["timestamp"] >= g_start) & (df["timestamp"] <= g.end) & df["service"].isin(g.services)
            analysis = analyze_incident(g, df[mask], graph, matcher)
            fp = _incident_fingerprint(g, window_seconds)
            inc = preserved.get(fp)
            if inc is None or inc.id in kept_ids:
                inc = _match_previous(g, old_incidents, kept_ids)
            if inc is not None:
                inc.fingerprint = fp
            else:
                inc = Incident(fingerprint=fp, status="open")
                db.add(inc)
            inc.title = analysis["title"]
            inc.summary = analysis["summary"]
            inc.severity = g.severity
            inc.confidence = analysis["confidence"]
            inc.started_at, inc.ended_at = g.start, g.end
            inc.services = g.services
            inc.categories = analysis["categories"]
            inc.root_cause = analysis["root_cause"]
            inc.timeline = analysis["timeline"]
            inc.run_id = run.id
            db.flush()
            kept_ids.add(inc.id)
            for a in g.anomalies:
                model_by_id[a.id].incident_id = inc.id
            incidents.append(inc)

        stale = [i for i in old_incidents if i.id not in kept_ids]
        stale_ids = [i.id for i in stale]
        for i in stale:
            db.delete(i)
        db.flush()

        for inc in incidents:
            text, source, _err = generate_narrative(incident_dict(inc, sample_logs_for(db, inc)), use_llm)
            inc.narrative, inc.narrative_source = text, source

        run.status = "completed"
        run.finished_at = utcnow()
        stats = {"logs_analyzed": int(len(df)), "anomalies_detected": len(detected),
                 "statistical_anomalies": sum(a.kind == "statistical" for a in detected),
                 "signature_anomalies": sum(a.kind == "signature" for a in detected),
                 "incidents": len(incidents)}
        run.stats = stats
        db.commit()

        # Vector indexing happens after commit so IDs are final; failures never break analysis.
        try:
            store.delete(stale_ids)
            for inc in incidents:
                store.upsert_incident(incident_dict(inc))
        except Exception as exc:  # pragma: no cover
            log.warning("vector indexing failed: %s", exc)

        cache = get_cache()
        cache.delete_prefix("stats:")
        cache.set_json("correlation:dependency_graph", graph.to_dict())
        cache.set_json("correlation:last_run", {"run_id": run.id, "range_start": start.isoformat(),
                                                "range_end": end.isoformat(), **stats, **params})
    except Exception as exc:
        db.rollback()
        run = db.get(AnalysisRun, run.id)
        if run is not None:
            run.status, run.error, run.finished_at = "failed", str(exc)[:2000], utcnow()
            db.commit()
        raise

    return {"run_id": run.id, "range_start": start, "range_end": end, **stats,
            "duration_ms": int((time.perf_counter() - t0) * 1000), "params": params}


def dependency_graph(db: Session) -> dict:
    cached = get_cache().get_json("correlation:dependency_graph")
    if cached:
        return cached
    end = db.scalar(select(func.max(LogEntry.timestamp)))
    if end is None:
        return {"nodes": [], "edges": []}
    rows = db.execute(select(LogEntry.trace_id, LogEntry.service, LogEntry.timestamp)
                      .where(LogEntry.timestamp >= end - timedelta(hours=6), LogEntry.trace_id.is_not(None))).all()
    df = pd.DataFrame(rows, columns=["trace_id", "service", "timestamp"])
    g = DependencyGraph.from_frame(df).to_dict()
    get_cache().set_json("correlation:dependency_graph", g, ttl=300)
    return g
