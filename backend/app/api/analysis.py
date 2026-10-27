from __future__ import annotations

from datetime import timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.data.generator import generate_logs
from app.database import get_db
from app.models import AnalysisRun, Anomaly, Incident, LogEntry
from app.schemas import AnalysisRequest, AnalysisSummary, GenerateRequest, IngestResult
from app.services.cache import broadcaster, get_cache
from app.services.embeddings import get_embedder
from app.services.ingestion import SEVERITIES, ingest_records
from app.services.pipeline import dependency_graph, run_analysis
from app.services.vector_store import get_vector_store

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analysis/run", response_model=AnalysisSummary, summary="Detect anomalies, correlate, run RCA")
def analyze(body: AnalysisRequest, db: Session = Depends(get_db)):
    if body.start and body.end and body.start >= body.end:
        raise HTTPException(status_code=422, detail="start must be before end")
    return AnalysisSummary(**run_analysis(db, **body.model_dump()))


@router.get("/analysis/runs")
def list_runs(limit: int = Query(default=20, le=200), db: Session = Depends(get_db)) -> list[dict]:
    runs = db.scalars(select(AnalysisRun).order_by(AnalysisRun.id.desc()).limit(limit)).all()
    return [{"id": r.id, "status": r.status, "started_at": r.started_at.isoformat() + "Z",
             "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
             "range_start": r.range_start.isoformat() + "Z" if r.range_start else None,
             "range_end": r.range_end.isoformat() + "Z" if r.range_end else None,
             "params": r.params, "stats": r.stats, "error": r.error} for r in runs]


@router.post("/samples/generate", response_model=IngestResult, status_code=201,
             summary="Generate and ingest realistic synthetic logs for a failure scenario")
def generate(body: GenerateRequest, db: Session = Depends(get_db)):
    from app.api.logs import _finish

    logs = generate_logs(body.scenario, body.minutes, body.requests_per_minute, body.seed, body.end_time)
    outcome = ingest_records(db, logs, source=f"generator:{body.scenario}")
    return _finish(db, outcome, analyze=body.analyze)


@router.get("/stats/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    cache = get_cache()
    if (hit := cache.get_json("stats:overview")) is not None:
        return hit
    total = db.scalar(select(func.count(LogEntry.id))) or 0
    sev = dict(db.execute(select(LogEntry.severity, func.count()).group_by(LogEntry.severity)).all())
    services = db.execute(select(LogEntry.service, func.count(),
                                 func.sum(case((LogEntry.severity.in_(["ERROR", "CRITICAL"]), 1), else_=0)))
                          .group_by(LogEntry.service)).all()
    inc_status = dict(db.execute(select(Incident.status, func.count()).group_by(Incident.status)).all())
    inc_sev = dict(db.execute(select(Incident.severity, func.count()).where(Incident.status != "resolved")
                              .group_by(Incident.severity)).all())
    anom_by_service = db.execute(select(Anomaly.service, Anomaly.kind, func.count())
                                 .group_by(Anomaly.service, Anomaly.kind)).all()
    rng = db.execute(select(func.min(LogEntry.timestamp), func.max(LogEntry.timestamp))).one()
    errors = sev.get("ERROR", 0) + sev.get("CRITICAL", 0)
    per_service: dict[str, dict] = {}
    for s, k, c in anom_by_service:
        per_service.setdefault(s, {"service": s, "statistical": 0, "signature": 0})[k] = c
    result = {
        "total_logs": total,
        "error_logs": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "by_severity": [{"severity": s, "count": sev.get(s, 0)} for s in SEVERITIES],
        "by_service": [{"service": s, "count": c, "errors": int(e or 0)} for s, c, e in services],
        "incidents": {"total": sum(inc_status.values()), "open": inc_status.get("open", 0),
                      "investigating": inc_status.get("investigating", 0),
                      "resolved": inc_status.get("resolved", 0), "active_by_severity": inc_sev},
        "anomalies": {"total": sum(c for _, _, c in anom_by_service),
                      "by_service": sorted(per_service.values(), key=lambda d: d["service"])},
        "time_range": {"start": rng[0].isoformat() + "Z" if rng[0] else None,
                       "end": rng[1].isoformat() + "Z" if rng[1] else None},
        "last_run": cache.get_json("correlation:last_run"),
    }
    cache.set_json("stats:overview", result, ttl=30)
    return result


@router.get("/stats/timeseries", summary="Log counts per time bucket and severity")
def timeseries(bucket_seconds: int = Query(default=60, ge=10, le=86400), service: str | None = None,
               hours: float = Query(default=24, gt=0, le=24 * 30), db: Session = Depends(get_db)) -> dict:
    cache = get_cache()
    key = f"stats:ts:{bucket_seconds}:{service}:{hours}"
    if (hit := cache.get_json(key)) is not None:
        return hit
    end = db.scalar(select(func.max(LogEntry.timestamp)))
    if end is None:
        return {"bucket_seconds": bucket_seconds, "points": [], "anomalies": []}
    start = end - timedelta(hours=hours)
    stmt = select(LogEntry.timestamp, LogEntry.severity).where(LogEntry.timestamp >= start)
    if service:
        stmt = stmt.where(LogEntry.service == service)
    df = pd.DataFrame(db.execute(stmt).all(), columns=["timestamp", "severity"])
    points: list[dict] = []
    if not df.empty:
        df["bucket"] = pd.to_datetime(df["timestamp"]).dt.floor(f"{bucket_seconds}s")
        pivot = df.pivot_table(index="bucket", columns="severity", values="timestamp", aggfunc="count", fill_value=0)
        full = pd.date_range(pivot.index.min(), pivot.index.max(), freq=f"{bucket_seconds}s")
        pivot = pivot.reindex(full, fill_value=0)
        for ts, row in pivot.iterrows():
            p = {"t": ts.isoformat() + "Z"}
            for s in SEVERITIES:
                p[s] = int(row.get(s, 0))
            p["total"] = sum(p[s] for s in SEVERITIES)
            points.append(p)
    a_stmt = select(Anomaly).where(Anomaly.window_start >= start)
    if service:
        a_stmt = a_stmt.where(Anomaly.service == service)
    anomalies = [{"id": a.id, "t": a.window_start.isoformat() + "Z", "end": a.window_end.isoformat() + "Z",
                  "service": a.service, "severity": a.severity, "kind": a.kind, "metric": a.metric,
                  "incident_id": a.incident_id} for a in db.scalars(a_stmt)]
    result = {"bucket_seconds": bucket_seconds, "points": points, "anomalies": anomalies}
    cache.set_json(key, result, ttl=30)
    return result


@router.get("/dependencies", summary="Service dependency graph inferred from trace IDs")
def dependencies(db: Session = Depends(get_db)) -> dict:
    return dependency_graph(db)


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    db_ok = True
    try:
        db.execute(select(1))
    except Exception:
        db_ok = False
    cache = get_cache()
    store = get_vector_store()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": s.app_name,
        "database": {"ok": db_ok, "dialect": db.get_bind().dialect.name},
        "cache": {"backend": cache.backend, "ok": cache.ping()},
        "vector_store": {"backend": store.backend, "indexed_incidents": store.count()},
        "embeddings": {"provider": get_embedder().name},
        "llm": {"provider": "groq", "configured": s.llm_enabled, "model": s.groq_model if s.llm_enabled else None},
        "live_subscribers": broadcaster.subscribers,
        "detection_defaults": {"window_seconds": s.window_seconds, "z_threshold": s.z_threshold,
                               "min_anomaly_count": s.min_anomaly_count,
                               "correlation_gap_seconds": s.correlation_gap_seconds},
    }


@router.post("/system/reset", summary="Delete all logs, anomalies, incidents and runs")
def reset(confirm: bool = False, db: Session = Depends(get_db)) -> dict:
    if not confirm:
        raise HTTPException(status_code=400, detail="pass confirm=true to delete all data")
    ids = list(db.scalars(select(Incident.id)))
    db.execute(delete(Anomaly))
    db.execute(delete(Incident))
    db.execute(delete(AnalysisRun))
    n = db.execute(delete(LogEntry)).rowcount
    db.commit()
    get_vector_store().delete(ids)
    cache = get_cache()
    cache.delete_prefix("stats:")
    cache.delete_prefix("correlation:")
    return {"deleted_logs": n, "deleted_incidents": len(ids)}
