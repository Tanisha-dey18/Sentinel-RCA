import json
from datetime import datetime

import pytest

from app.services.ingestion import (NormalizationError, detect_format, ingest_records, make_template,
                                    normalize_record, normalize_severity, parse_content, parse_timestamp)


@pytest.mark.parametrize("value,expected", [
    ("2024-05-01T12:00:00Z", datetime(2024, 5, 1, 12, 0, 0)),
    ("2024-05-01T14:00:00+02:00", datetime(2024, 5, 1, 12, 0, 0)),
    ("2024-05-01 12:00:00,250", datetime(2024, 5, 1, 12, 0, 0, 250000)),
    (1714564800, datetime(2024, 5, 1, 12, 0, 0)),
    (1714564800000, datetime(2024, 5, 1, 12, 0, 0)),
    ("1714564800", datetime(2024, 5, 1, 12, 0, 0)),
])
def test_parse_timestamp_formats(value, expected):
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize("bad", [None, "", "not a date", "1800-01-01T00:00:00", [1, 2]])
def test_parse_timestamp_rejects(bad):
    with pytest.raises(NormalizationError):
        parse_timestamp(bad)


def test_severity_aliases():
    assert normalize_severity("warn") == "WARNING"
    assert normalize_severity("FATAL") == "CRITICAL"
    assert normalize_severity(3) == "ERROR"
    assert normalize_severity(None) == "INFO"
    with pytest.raises(NormalizationError):
        normalize_severity("loud")


def test_normalize_aliases_and_metadata():
    log = normalize_record({"@timestamp": "2024-05-01T12:00:00Z", "level": "err", "app": "api",
                            "msg": "boom", "hostname": "h1", "traceId": "abc", "latency_ms": 12,
                            "meta": {"region": "eu"}})
    assert (log.severity, log.service, log.host, log.trace_id) == ("ERROR", "api", "h1", "abc")
    assert log.metadata == {"region": "eu", "latency_ms": 12}


def test_normalize_missing_optional_fields():
    log = normalize_record({"timestamp": "2024-05-01T12:00:00Z", "message": "hello"})
    assert log.service == "unknown" and log.severity == "INFO" and log.host is None and log.trace_id is None


@pytest.mark.parametrize("raw,err", [
    ({"timestamp": "2024-05-01T12:00:00Z"}, "missing message"),
    ({"message": "x"}, "missing timestamp"),
    ("just a string", "must be an object"),
])
def test_normalize_errors(raw, err):
    with pytest.raises(NormalizationError, match=err):
        normalize_record(raw)


def test_template_masks_variables():
    a = make_template("Query took 532ms for user 'bob' from 10.0.0.1 id=550e8400-e29b-41d4-a716-446655440000")
    b = make_template("Query took 12ms for user 'alice' from 10.9.9.9 id=123e4567-e89b-12d3-a456-426614174000")
    assert a == b
    assert "<NUM>" in a and "<IP>" in a and "<UUID>" in a and "<STR>" in a


def test_detect_and_parse_formats():
    jsonl = b'{"timestamp": 1, "message": "a"}\n{"timestamp": 2, "message": "b"}\nnot json\n'
    assert detect_format("x.jsonl", jsonl) == "jsonl"
    records, errors = parse_content(jsonl, "jsonl")
    assert len(records) == 2 and errors[0]["index"] == 2

    arr = json.dumps({"logs": [{"timestamp": 1, "message": "a"}]}).encode()
    assert detect_format("upload.bin", arr) == "json"
    assert len(parse_content(arr, "json")[0]) == 1

    csv_bytes = b"timestamp,severity,service,message,metadata\n2024-05-01T12:00:00Z,ERROR,api,boom,\"{\"\"k\"\": 1}\"\n"
    assert detect_format("x.csv", csv_bytes) == "csv"
    records, errors = parse_content(csv_bytes, "csv")
    assert not errors
    assert normalize_record(records[0]).metadata == {"k": 1}


def test_ingest_dedup_and_errors(db):
    rec = {"timestamp": "2024-05-01T12:00:00Z", "message": "hello", "service": "api"}
    out = ingest_records(db, [rec, dict(rec), {"message": "no ts"}])
    assert (out.accepted, out.duplicates, out.rejected) == (1, 1, 1)
    assert out.errors[0]["index"] == 2
    again = ingest_records(db, [rec])
    assert (again.accepted, again.duplicates) == (0, 1)
