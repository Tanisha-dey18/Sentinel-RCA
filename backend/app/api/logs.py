from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import LogEntry
from app.schemas import AnalysisSummary, IngestResult, LogBatchIn, LogIn, LogOut, LogPage
from app.services.cache import broadcaster, get_cache
from app.services.ingestion import SEVERITIES, IngestOutcome, detect_format, ingest_records, parse_content
from app.services.pipeline import run_analysis

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _finish(db: Session, outcome: IngestOutcome, parse_errors: list[dict] | None = None,
            analyze: bool = False) -> IngestResult:
    errors = (parse_errors or []) + outcome.errors
    analysis = None
    if outcome.accepted:
        cache = get_cache()
        cache.delete_prefix("stats:")
        events = [{"type": "ingest", "accepted": outcome.accepted, "first_id": outcome.inserted_ids[0],
                   "last_id": outcome.inserted_ids[-1]}]
        cache.append_stream(events)
        for e in events:
            broadcaster.publish(e)
        if analyze:
            analysis = AnalysisSummary(**run_analysis(db))
    return IngestResult(
        received=outcome.received + len(parse_errors or []), accepted=outcome.accepted,
        duplicates=outcome.duplicates, rejected=len(errors), errors=errors[:200],
        first_timestamp=outcome.first_timestamp, last_timestamp=outcome.last_timestamp, analysis=analysis,
    )


@router.post("", response_model=IngestResult, status_code=201, summary="Ingest a single log record")
def ingest_one(log: LogIn, analyze: bool = False, db: Session = Depends(get_db)) -> IngestResult:
    raw = log.model_dump(exclude_none=True)
    outcome = ingest_records(db, [raw], source="api")
    if outcome.errors:
        raise HTTPException(status_code=422, detail=outcome.errors[0]["error"])
    return _finish(db, outcome, analyze=analyze)


@router.post("/batch", response_model=IngestResult, status_code=201, summary="Ingest a batch of log records")
def ingest_batch(body: LogBatchIn, analyze: bool = False, db: Session = Depends(get_db)) -> IngestResult:
    limit = get_settings().max_batch_size
    if len(body.logs) > limit:
        raise HTTPException(status_code=413, detail=f"batch too large: {len(body.logs)} > {limit}")
    return _finish(db, ingest_records(db, body.logs, source="batch"), analyze=analyze)


@router.post("/upload", response_model=IngestResult, status_code=201, summary="Upload a JSON, JSONL or CSV file")
async def upload(file: UploadFile = File(...), format: str | None = Form(default=None),
                 analyze: bool = Form(default=False), db: Session = Depends(get_db)) -> IngestResult:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"file exceeds {get_settings().max_upload_mb} MB")
    if not content.strip():
        raise HTTPException(status_code=400, detail="file is empty")
    fmt = (format or "").lower() or detect_format(file.filename, content)
    if fmt not in ("json", "jsonl", "csv"):
        raise HTTPException(status_code=400, detail=f"unsupported format '{fmt}' (use json, jsonl or csv)")
    records, parse_errors = parse_content(content, fmt)
    if not records and parse_errors:
        raise HTTPException(status_code=422, detail={"message": "no parseable records", "errors": parse_errors[:20]})
    outcome = await asyncio.to_thread(ingest_records, db, records, f"upload:{fmt}")
    return await asyncio.to_thread(_finish, db, outcome, parse_errors, analyze)


@router.get("", response_model=LogPage, summary="Search and filter logs")
def list_logs(
    service: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    start: datetime | None = None,
    end: datetime | None = None,
    q: str | None = Query(default=None, max_length=500, description="keyword (case-insensitive substring)"),
    trace_id: str | None = None,
    ids: str | None = Query(default=None, description="comma-separated log IDs"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> LogPage:
    stmt = select(LogEntry)
    if service:
        stmt = stmt.where(LogEntry.service.in_(service))
    if severity:
        stmt = stmt.where(LogEntry.severity.in_([s.upper() for s in severity]))
    if start:
        stmt = stmt.where(LogEntry.timestamp >= _naive(start))
    if end:
        stmt = stmt.where(LogEntry.timestamp <= _naive(end))
    if trace_id:
        stmt = stmt.where(LogEntry.trace_id == trace_id)
    if ids:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()][:1000]
        except ValueError:
            raise HTTPException(status_code=422, detail="ids must be comma-separated integers") from None
        stmt = stmt.where(LogEntry.id.in_(id_list))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(LogEntry.message).like(like), func.lower(LogEntry.service).like(like),
                              func.lower(LogEntry.host).like(like), LogEntry.trace_id == q))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order_col = LogEntry.timestamp.asc() if order == "asc" else LogEntry.timestamp.desc()
    rows = db.scalars(stmt.order_by(order_col, LogEntry.id).limit(limit).offset(offset)).all()
    return LogPage(items=[LogOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/facets", summary="Distinct services, hosts and severities")
def facets(db: Session = Depends(get_db)) -> dict:
    services = db.execute(select(LogEntry.service, func.count()).group_by(LogEntry.service)
                          .order_by(LogEntry.service)).all()
    sevs = dict(db.execute(select(LogEntry.severity, func.count()).group_by(LogEntry.severity)).all())
    rng = db.execute(select(func.min(LogEntry.timestamp), func.max(LogEntry.timestamp))).one()
    return {
        "services": [{"name": s, "count": c} for s, c in services],
        "severities": [{"name": s, "count": sevs.get(s, 0)} for s in SEVERITIES],
        "time_range": {"start": rng[0].isoformat() + "Z" if rng[0] else None,
                       "end": rng[1].isoformat() + "Z" if rng[1] else None},
    }


@router.get("/stream", summary="Server-sent events: live ingest notifications")
async def stream(request: Request) -> StreamingResponse:
    queue = broadcaster.subscribe()

    async def gen():
        try:
            yield "event: ready\ndata: {}\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{log_id}", response_model=LogOut)
def get_log(log_id: int, db: Session = Depends(get_db)) -> LogOut:
    row = db.get(LogEntry, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="log not found")
    return LogOut.model_validate(row)


def _naive(d: datetime) -> datetime:
    from datetime import timezone

    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
