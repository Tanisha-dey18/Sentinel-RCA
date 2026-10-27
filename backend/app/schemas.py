"""Pydantic request/response schemas."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator


def _iso_utc(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d.isoformat(timespec="milliseconds") + "Z"


UTCDateTime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]

SeverityLevel = Literal["low", "medium", "high", "critical"]
IncidentStatus = Literal["open", "investigating", "resolved"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- logs
class LogIn(BaseModel):
    """A single log record. Unknown extra fields are kept as metadata."""

    model_config = ConfigDict(extra="allow")

    timestamp: datetime | str | int | float
    message: str = Field(min_length=1)
    severity: str = "INFO"
    service: str = "unknown"
    host: str | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogBatchIn(BaseModel):
    logs: list[dict[str, Any]] = Field(min_length=1)


class LogOut(ORMModel):
    id: int
    timestamp: UTCDateTime
    severity: str
    service: str
    message: str
    template: str
    host: str | None
    trace_id: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    source: str
    ingested_at: UTCDateTime


class LogPage(BaseModel):
    items: list[LogOut]
    total: int
    limit: int
    offset: int


class IngestError(BaseModel):
    index: int
    error: str
    record: str | None = None


class IngestResult(BaseModel):
    received: int
    accepted: int
    duplicates: int
    rejected: int
    errors: list[IngestError]
    first_timestamp: UTCDateTime | None = None
    last_timestamp: UTCDateTime | None = None
    analysis: AnalysisSummary | None = None


# ---------------------------------------------------------------- signatures
class SignatureBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    pattern: str = Field(min_length=1)
    severity: SeverityLevel = "medium"
    min_count: int = Field(default=1, ge=1, le=100000)
    description: str = ""
    enabled: bool = True

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return v


class SignatureCreate(SignatureBase):
    key: str = Field(pattern=r"^[a-z0-9_]{2,64}$")


class SignatureUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    pattern: str | None = None
    severity: SeverityLevel | None = None
    min_count: int | None = Field(default=None, ge=1)
    description: str | None = None
    enabled: bool | None = None

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from exc
        return v


class SignatureOut(ORMModel):
    id: int
    key: str
    name: str
    category: str
    pattern: str
    severity: str
    min_count: int
    description: str
    enabled: bool
    builtin: bool


class SignatureTestIn(BaseModel):
    pattern: str
    messages: list[str] = Field(min_length=1, max_length=500)


class SignatureTestOut(BaseModel):
    results: list[dict[str, Any]]


# ---------------------------------------------------------------- anomalies
class AnomalyOut(ORMModel):
    id: int
    kind: str
    detector: str
    metric: str
    service: str
    signature_key: str | None
    category: str | None
    window_start: UTCDateTime
    window_end: UTCDateTime
    observed_value: float
    baseline_median: float | None
    mad: float | None
    robust_z: float | None
    score: float
    severity: str
    description: str
    evidence_log_ids: list[int]
    evidence_count: int
    trace_ids: list[str]
    incident_id: int | None


class AnomalyPage(BaseModel):
    items: list[AnomalyOut]
    total: int


# ---------------------------------------------------------------- incidents
class IncidentListItem(ORMModel):
    id: int
    title: str
    summary: str
    status: str
    severity: str
    confidence: float
    started_at: UTCDateTime
    ended_at: UTCDateTime
    services: list[str]
    categories: list[str]
    anomaly_count: int = 0
    created_at: UTCDateTime
    updated_at: UTCDateTime


class IncidentDetail(IncidentListItem):
    root_cause: dict[str, Any]
    timeline: list[dict[str, Any]]
    narrative: str
    narrative_source: str
    notes: str
    resolved_at: UTCDateTime | None
    anomalies: list[AnomalyOut]
    evidence_logs: list[LogOut] = Field(default_factory=list)


class IncidentUpdate(BaseModel):
    status: IncidentStatus | None = None
    notes: str | None = None


class SimilarIncident(BaseModel):
    id: int
    title: str
    status: str
    severity: str
    services: list[str]
    started_at: UTCDateTime
    similarity: float
    root_cause_title: str | None = None
    notes: str = ""


class ExplainRequest(BaseModel):
    use_llm: bool = True


# ---------------------------------------------------------------- analysis
class AnalysisRequest(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    window_seconds: int | None = Field(default=None, ge=5, le=3600)
    z_threshold: float | None = Field(default=None, ge=1.0, le=50.0)
    min_anomaly_count: int | None = Field(default=None, ge=1)
    correlation_gap_seconds: int | None = Field(default=None, ge=0, le=86400)
    use_llm: bool | None = None


class AnalysisSummary(BaseModel):
    run_id: int
    range_start: UTCDateTime | None
    range_end: UTCDateTime | None
    logs_analyzed: int
    anomalies_detected: int
    statistical_anomalies: int
    signature_anomalies: int
    incidents: int
    duration_ms: int
    params: dict[str, Any]


class GenerateRequest(BaseModel):
    scenario: Literal[
        "normal", "db_timeout", "http_500", "memory_pressure", "auth_failure", "cascading_failure", "mixed"
    ] = "mixed"
    minutes: int = Field(default=60, ge=5, le=24 * 60)
    requests_per_minute: int = Field(default=40, ge=1, le=2000)
    seed: int | None = None
    end_time: datetime | None = None
    analyze: bool = True


IngestResult.model_rebuild()
