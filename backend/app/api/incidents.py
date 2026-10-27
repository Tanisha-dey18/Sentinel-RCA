from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Anomaly, Incident, LogEntry, utcnow
from app.schemas import (AnomalyOut, AnomalyPage, ExplainRequest, IncidentDetail, IncidentListItem, IncidentUpdate,
                         LogOut, SimilarIncident)
from app.services.cache import get_cache
from app.services.narrative import generate_narrative
from app.services.pipeline import incident_dict, sample_logs_for
from app.services.vector_store import get_vector_store

router = APIRouter(prefix="/api", tags=["incidents"])


def _list_item(inc: Incident, count: int) -> IncidentListItem:
    item = IncidentListItem.model_validate(inc)
    item.anomaly_count = count
    return item


def _get(db: Session, incident_id: int) -> Incident:
    inc = db.scalar(select(Incident).options(selectinload(Incident.anomalies)).where(Incident.id == incident_id))
    if inc is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return inc


def _evidence_ids(inc: Incident, limit: int = 150) -> list[int]:
    rc = inc.root_cause or {}
    ids: list[int] = []
    for f in rc.get("facts", []):
        ids += f.get("evidence", {}).get("log_ids", [])
    for h in rc.get("hypotheses", [])[:3]:
        ids += h.get("evidence", {}).get("log_ids", [])
    for c in rc.get("causal_chain", []):
        ids += c["evidence"].get("cause_log_ids", []) + c["evidence"].get("effect_log_ids", [])
    for e in inc.timeline or []:
        ids += e.get("log_ids", [])
    for t in rc.get("propagation_traces", [])[:5]:
        ids += t.get("log_ids", [])
    for a in inc.anomalies:
        ids += a.evidence_log_ids[:5]
    return list(dict.fromkeys(int(i) for i in ids))[:limit]


@router.get("/incidents", response_model=list[IncidentListItem])
def list_incidents(status: list[str] | None = Query(default=None), severity: list[str] | None = Query(default=None),
                   service: str | None = None, q: str | None = None,
                   limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    stmt = select(Incident)
    if status:
        stmt = stmt.where(Incident.status.in_(status))
    if severity:
        stmt = stmt.where(Incident.severity.in_(severity))
    if q:
        stmt = stmt.where(func.lower(Incident.title).like(f"%{q.lower()}%"))
    rows = db.scalars(stmt.order_by(Incident.started_at.desc()).limit(limit)).all()
    if service:
        rows = [r for r in rows if service in (r.services or [])]
    counts = dict(db.execute(select(Anomaly.incident_id, func.count()).where(
        Anomaly.incident_id.in_([r.id for r in rows])).group_by(Anomaly.incident_id)).all())
    return [_list_item(r, counts.get(r.id, 0)) for r in rows]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    inc = _get(db, incident_id)
    ids = _evidence_ids(inc)
    logs = db.scalars(select(LogEntry).where(LogEntry.id.in_(ids)).order_by(LogEntry.timestamp)).all() if ids else []
    detail = IncidentDetail.model_validate(inc)
    detail.anomaly_count = len(inc.anomalies)
    detail.evidence_logs = [LogOut.model_validate(l) for l in logs]
    return detail


@router.patch("/incidents/{incident_id}", response_model=IncidentListItem)
def update_incident(incident_id: int, body: IncidentUpdate, db: Session = Depends(get_db)):
    inc = _get(db, incident_id)
    if body.status is not None:
        inc.status = body.status
        inc.resolved_at = utcnow() if body.status == "resolved" else None
    if body.notes is not None:
        inc.notes = body.notes[:20000]
    db.commit()
    get_cache().delete_prefix("stats:")
    try:
        get_vector_store().upsert_incident(incident_dict(inc))
    except Exception:
        pass
    return _list_item(inc, len(inc.anomalies))


@router.get("/incidents/{incident_id}/timeline")
def incident_timeline(incident_id: int, db: Session = Depends(get_db)) -> list[dict]:
    return _get(db, incident_id).timeline


@router.get("/incidents/{incident_id}/similar", response_model=list[SimilarIncident])
def similar_incidents(incident_id: int, limit: int = Query(default=5, ge=1, le=20), db: Session = Depends(get_db)):
    inc = _get(db, incident_id)
    hits = get_vector_store().similar(incident_dict(inc), limit=limit)
    if not hits:
        return []
    by_id = {i.id: i for i in db.scalars(select(Incident).where(Incident.id.in_([h[0] for h in hits])))}
    out = []
    for pid, score in hits:
        other = by_id.get(pid)
        if other is None:
            continue
        primary = (other.root_cause or {}).get("primary") or {}
        out.append(SimilarIncident(id=other.id, title=other.title, status=other.status, severity=other.severity,
                                   services=other.services, started_at=other.started_at,
                                   similarity=round(score, 4), root_cause_title=primary.get("title"),
                                   notes=other.notes or ""))
    return out


@router.post("/incidents/{incident_id}/explain")
def explain_incident(incident_id: int, body: ExplainRequest, db: Session = Depends(get_db)) -> dict:
    inc = _get(db, incident_id)
    text, source, error = generate_narrative(incident_dict(inc, sample_logs_for(db, inc)), body.use_llm)
    inc.narrative, inc.narrative_source = text, source
    db.commit()
    return {"narrative": text, "narrative_source": source, "warning": error}


@router.get("/anomalies", response_model=AnomalyPage)
def list_anomalies(service: str | None = None, kind: str | None = None, severity: list[str] | None = Query(None),
                   incident_id: int | None = None, unassigned: bool = False,
                   limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0),
                   db: Session = Depends(get_db)):
    stmt = select(Anomaly)
    if service:
        stmt = stmt.where(Anomaly.service == service)
    if kind:
        stmt = stmt.where(Anomaly.kind == kind)
    if severity:
        stmt = stmt.where(Anomaly.severity.in_(severity))
    if incident_id is not None:
        stmt = stmt.where(Anomaly.incident_id == incident_id)
    if unassigned:
        stmt = stmt.where(Anomaly.incident_id.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Anomaly.window_start.desc()).limit(limit).offset(offset)).all()
    return AnomalyPage(items=[AnomalyOut.model_validate(r) for r in rows], total=total)
