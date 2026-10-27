from datetime import datetime

import pytest
from sqlalchemy import select

from app.data.generator import generate_logs
from app.models import Incident, LogEntry
from app.services.ingestion import ingest_records
from app.services.narrative import deterministic_narrative, generate_narrative
from app.services.pipeline import incident_dict, run_analysis

END = datetime(2024, 5, 1, 13, 0, 0)


def _analyze(db, scenario):
    ingest_records(db, generate_logs(scenario, minutes=60, requests_per_minute=40, seed=11, end_time=END), "test")
    run_analysis(db)
    return db.scalars(select(Incident).order_by(Incident.confidence.desc())).all()


@pytest.mark.parametrize("scenario,service,signature", [
    ("db_timeout", "orders-db", {"db_pool_exhausted", "db_timeout"}),
    ("memory_pressure", "inventory-service", {"memory_pressure", "out_of_memory"}),
    ("http_500", "payments-service", {"unhandled_exception"}),
    ("auth_failure", "auth-service", {"auth_failure"}),
])
def test_root_cause_identified(db, scenario, service, signature):
    incidents = _analyze(db, scenario)
    assert incidents, "expected at least one incident"
    primary = incidents[0].root_cause["primary"]
    assert primary["service"] == service
    assert primary["signature_key"] in signature
    assert incidents[0].confidence >= 0.45


def test_normal_traffic_produces_no_incidents(db):
    assert _analyze(db, "normal") == []


def test_every_conclusion_is_traceable(db):
    inc = _analyze(db, "memory_pressure")[0]
    rc = inc.root_cause
    known_ids = set(db.scalars(select(LogEntry.id)))
    for fact in rc["facts"]:
        assert fact["evidence"], fact
        assert set(fact["evidence"].get("log_ids", [])) <= known_ids
    for h in rc["hypotheses"]:
        assert h["evidence"]["anomaly_ids"]
        assert h["reasoning"]
        assert set(h["evidence"]["log_ids"]) <= known_ids
    kinds = {e["type"] for e in inc.timeline}
    assert {"fact", "signal", "inference"} <= kinds
    assert [e["timestamp"] for e in inc.timeline] == sorted(e["timestamp"] for e in inc.timeline) or \
        inc.timeline[0]["kind"] == "incident_start"
    chain = {(c["cause"], c["effect"]) for c in rc["causal_chain"]}
    assert ("memory_pressure", "out_of_memory") in chain


def test_reanalysis_preserves_incident_state(db):
    inc = _analyze(db, "db_timeout")[0]
    inc.status, inc.notes = "investigating", "looking at pg locks"
    db.commit()
    run_analysis(db)
    db.expire_all()
    again = db.get(Incident, inc.id)
    assert again is not None and again.status == "investigating" and again.notes == "looking at pg locks"


def test_reanalysis_keeps_identity_when_membership_shifts(db):
    """A new baseline can add/drop a peripheral service; the incident must keep its id and state."""
    inc = _analyze(db, "db_timeout")[0]
    inc.status, inc.notes = "investigating", "pool sizing"
    inc.services = inc.services + ["inventory-service"]  # as if a prior run had grouped one more service
    inc.fingerprint = "stale-fingerprint"
    db.commit()
    run_analysis(db)
    db.expire_all()
    again = db.get(Incident, inc.id)
    assert again is not None and again.status == "investigating" and again.notes == "pool sizing"
    assert "inventory-service" not in again.services


def test_narrative_fallback_without_llm(db):
    inc = _analyze(db, "http_500")[0]
    text, source, warning = generate_narrative(incident_dict(inc), use_llm=True)
    assert source == "deterministic" and warning
    assert "### Probable root cause (inference)" in text and "[F1]" in text


def test_narrative_llm_failure_falls_back(db, monkeypatch):
    from app.config import get_settings
    from app.services import narrative

    inc = _analyze(db, "http_500")[0]
    monkeypatch.setattr(get_settings(), "groq_api_key", "test-key")

    def boom(_):
        raise RuntimeError("network down")

    monkeypatch.setattr(narrative, "llm_narrative", boom)
    text, source, warning = generate_narrative(incident_dict(inc), use_llm=True)
    assert source == "deterministic" and "network down" in warning
    assert text == deterministic_narrative(incident_dict(inc))
