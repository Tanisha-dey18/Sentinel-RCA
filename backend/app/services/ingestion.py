"""Log parsing, normalization, deduplication and persistence."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models import LogEntry, utcnow

SEVERITIES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

SEVERITY_ALIASES = {
    "trace": "DEBUG", "debug": "DEBUG", "dbg": "DEBUG", "verbose": "DEBUG",
    "info": "INFO", "information": "INFO", "notice": "INFO", "informational": "INFO",
    "warn": "WARNING", "warning": "WARNING",
    "err": "ERROR", "error": "ERROR", "severe": "ERROR",
    "crit": "CRITICAL", "critical": "CRITICAL", "fatal": "CRITICAL", "emerg": "CRITICAL",
    "emergency": "CRITICAL", "alert": "CRITICAL", "panic": "CRITICAL",
}
# syslog numeric levels
SEVERITY_ALIASES.update({"0": "CRITICAL", "1": "CRITICAL", "2": "CRITICAL", "3": "ERROR",
                         "4": "WARNING", "5": "INFO", "6": "INFO", "7": "DEBUG"})

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "@timestamp", "ts", "time", "datetime", "date", "eventTime", "event_time"),
    "severity": ("severity", "level", "lvl", "log_level", "loglevel", "levelname", "priority"),
    "service": ("service", "service_name", "serviceName", "app", "application", "component", "logger", "source"),
    "message": ("message", "msg", "log", "text", "event", "body"),
    "host": ("host", "hostname", "host_name", "instance", "node", "pod"),
    "trace_id": ("trace_id", "traceId", "traceid", "trace", "request_id", "requestId", "correlation_id"),
    "metadata": ("metadata", "meta", "attributes", "fields", "context"),
}

_TEMPLATE_MASKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<HEX>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<HEX>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<EMAIL>"),
    (re.compile(r"(['\"])(?:(?!\1).){1,200}\1"), "<STR>"),
    (re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:ms|s|m|h|kb|mb|gb|%)?\b", re.I), "<NUM>"),
]

MAX_MESSAGE_LEN = 16_000


class NormalizationError(ValueError):
    """Raised when a record cannot be converted to the internal log schema."""


@dataclass
class NormalizedLog:
    timestamp: datetime
    severity: str
    service: str
    message: str
    template: str
    host: str | None
    trace_id: str | None
    metadata: dict[str, Any]
    fingerprint: str


@dataclass
class IngestOutcome:
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    inserted_ids: list[int] = field(default_factory=list)

    @property
    def rejected(self) -> int:
        return len(self.errors)


# ------------------------------------------------------------------ helpers
def parse_timestamp(value: Any) -> datetime:
    """Parse ISO-8601 strings, epoch seconds/milliseconds/microseconds and a few common formats.

    Returns a naive datetime in UTC.
    """
    if value is None or value == "":
        raise NormalizationError("missing timestamp")
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip())):
        num = float(value)
        if num > 1e17:  # nanoseconds
            num /= 1e9
        elif num > 1e14:  # microseconds
            num /= 1e6
        elif num > 1e11:  # milliseconds
            num /= 1e3
        try:
            dt = datetime.fromtimestamp(num, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise NormalizationError(f"invalid epoch timestamp: {value!r}") from exc
    elif isinstance(value, str):
        s = value.strip()
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%d/%b/%Y:%H:%M:%S %z", "%Y/%m/%d %H:%M:%S",
                        "%b %d %Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
                try:
                    dt = datetime.strptime(value.strip(), fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                raise NormalizationError(f"unrecognized timestamp format: {value!r}") from None
    else:
        raise NormalizationError(f"unsupported timestamp type: {type(value).__name__}")

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if not (1990 <= dt.year <= 2200):
        raise NormalizationError(f"timestamp out of range: {value!r}")
    return dt


def normalize_severity(value: Any) -> str:
    if value is None or value == "":
        return "INFO"
    key = str(value).strip().lower()
    if key in SEVERITY_ALIASES:
        return SEVERITY_ALIASES[key]
    raise NormalizationError(f"unknown severity: {value!r}")


def make_template(message: str) -> str:
    t = message
    for pattern, repl in _TEMPLATE_MASKS:
        t = pattern.sub(repl, t)
    return re.sub(r"\s+", " ", t).strip()[:1000]


def _pick(raw: dict[str, Any], canonical: str) -> tuple[str | None, Any]:
    for alias in FIELD_ALIASES[canonical]:
        if alias in raw and raw[alias] not in (None, ""):
            return alias, raw[alias]
    return None, None


def _clean_str(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:max_len] if s else None


def fingerprint(ts: datetime, severity: str, service: str, message: str, host: str | None, trace_id: str | None) -> str:
    raw = "\x1f".join([ts.isoformat(), severity, service, host or "", trace_id or "", message])
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def normalize_record(raw: Any) -> NormalizedLog:
    """Convert an arbitrary dict into the normalized internal log schema."""
    if not isinstance(raw, dict):
        raise NormalizationError(f"record must be an object, got {type(raw).__name__}")

    used: set[str] = set()
    values: dict[str, Any] = {}
    for canonical in FIELD_ALIASES:
        alias, val = _pick(raw, canonical)
        if alias:
            used.add(alias)
        values[canonical] = val

    message = values["message"]
    if message is None or (isinstance(message, str) and not message.strip()):
        raise NormalizationError("missing message")
    if not isinstance(message, str):
        message = json.dumps(message, default=str)
    message = message.strip()[:MAX_MESSAGE_LEN]

    ts = parse_timestamp(values["timestamp"])
    severity = normalize_severity(values["severity"])
    service = _clean_str(values["service"], 128) or "unknown"
    host = _clean_str(values["host"], 255)
    trace_id = _clean_str(values["trace_id"], 128)

    metadata: dict[str, Any] = {}
    meta_val = values["metadata"]
    if isinstance(meta_val, str):
        try:
            meta_val = json.loads(meta_val)
        except json.JSONDecodeError:
            meta_val = {"raw": meta_val}
    if isinstance(meta_val, dict):
        metadata.update(meta_val)
    elif meta_val is not None:
        metadata["value"] = meta_val
    # Unknown top-level fields are preserved as metadata.
    for k, v in raw.items():
        if k not in used and v not in (None, ""):
            metadata.setdefault(k, v)

    return NormalizedLog(
        timestamp=ts,
        severity=severity,
        service=service,
        message=message,
        template=make_template(message),
        host=host,
        trace_id=trace_id,
        metadata=metadata,
        fingerprint=fingerprint(ts, severity, service, message, host, trace_id),
    )


# ------------------------------------------------------------------ file parsing
def detect_format(filename: str | None, content: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return "jsonl"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".json"):
        return "json"
    head = content.lstrip()[:1]
    if head == b"[":
        return "json"
    if head == b"{":
        # A JSON object on the first line followed by more objects -> JSONL
        first_line = content.lstrip().split(b"\n", 1)[0].strip()
        try:
            json.loads(first_line)
            return "jsonl" if b"\n" in content.strip() else "json"
        except json.JSONDecodeError:
            return "json"
    return "csv"


def parse_content(content: bytes, fmt: str) -> tuple[list[Any], list[dict[str, Any]]]:
    """Return (records, parse_errors). Each error carries the 0-based record/line index."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    records: list[Any] = []
    errors: list[dict[str, Any]] = []

    if fmt == "jsonl":
        for i, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append({"index": i, "error": f"invalid JSON on line {i + 1}: {exc.msg}", "record": line[:200]})
    elif fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append({"index": 0, "error": f"invalid JSON document: {exc.msg} (line {exc.lineno})", "record": None})
            return records, errors
        if isinstance(data, dict) and isinstance(data.get("logs"), list):
            data = data["logs"]
        records = data if isinstance(data, list) else [data]
    elif fmt == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            errors.append({"index": 0, "error": "CSV file has no header row", "record": None})
            return records, errors
        for row in reader:
            records.append({k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k})
    else:
        errors.append({"index": 0, "error": f"unsupported format: {fmt}", "record": None})
    return records, errors


# ------------------------------------------------------------------ persistence
def ingest_records(db: Session, records: list[Any], source: str = "api", index_offset: int = 0) -> IngestOutcome:
    """Validate, normalize, dedupe and persist records."""
    out = IngestOutcome(received=len(records))
    normalized: list[NormalizedLog] = []
    seen: set[str] = set()

    for i, raw in enumerate(records):
        try:
            log = normalize_record(raw)
        except NormalizationError as exc:
            out.errors.append({"index": i + index_offset, "error": str(exc), "record": _preview(raw)})
            continue
        if log.fingerprint in seen:
            out.duplicates += 1
            continue
        seen.add(log.fingerprint)
        normalized.append(log)

    # Deduplicate against already stored logs.
    existing: set[str] = set()
    fps = [n.fingerprint for n in normalized]
    for chunk_start in range(0, len(fps), 500):
        chunk = fps[chunk_start:chunk_start + 500]
        existing.update(db.scalars(select(LogEntry.fingerprint).where(LogEntry.fingerprint.in_(chunk))))

    now = utcnow()
    rows: list[dict[str, Any]] = []
    for n in normalized:
        if n.fingerprint in existing:
            out.duplicates += 1
            continue
        rows.append({
            "timestamp": n.timestamp, "severity": n.severity, "service": n.service, "message": n.message,
            "template": n.template, "host": n.host, "trace_id": n.trace_id, "metadata_": n.metadata,
            "fingerprint": n.fingerprint, "source": source, "ingested_at": now,
        })

    if rows:
        # Core bulk INSERT ... RETURNING (batched "insertmanyvalues") is far faster than ORM unit-of-work.
        for chunk_start in range(0, len(rows), 2000):
            chunk = rows[chunk_start:chunk_start + 2000]
            out.inserted_ids += list(db.scalars(insert(LogEntry).returning(LogEntry.id), chunk))
        db.commit()
        out.inserted_ids.sort()
        out.accepted = len(rows)
        out.first_timestamp = min(r["timestamp"] for r in rows)
        out.last_timestamp = max(r["timestamp"] for r in rows)
    return out


def _preview(raw: Any) -> str:
    try:
        s = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        s = repr(raw)
    return s[:200]
