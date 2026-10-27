"""Sentinel RCA — FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api import analysis, incidents, logs, signatures
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import LogEntry
from app.services.signatures import seed_default_signatures

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sentinel")


def bootstrap() -> None:
    init_db()
    s = get_settings()
    with SessionLocal() as db:
        seed_default_signatures(db)
        if s.seed_sample_data and not db.scalar(select(func.count(LogEntry.id))):
            from app.data.generator import generate_logs
            from app.services.ingestion import ingest_records
            from app.services.pipeline import run_analysis

            log.info("Empty database: seeding synthetic 'mixed' scenario and running analysis...")
            ingest_records(db, generate_logs("mixed", minutes=120, requests_per_minute=40, seed=7),
                           source="generator:seed")
            summary = run_analysis(db)
            log.info("Seed analysis: %s", {k: summary[k] for k in ("logs_analyzed", "anomalies_detected",
                                                                     "incidents")})


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    yield


settings = get_settings()
app = FastAPI(
    title="Sentinel RCA",
    version="1.0.0",
    description="AI log anomaly detection, incident correlation and evidence-based root-cause analysis.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

for r in (logs.router, incidents.router, signatures.router, analysis.router):
    app.include_router(r)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"name": "Sentinel RCA", "docs": "/docs", "health": "/api/health"}
