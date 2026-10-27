from datetime import datetime, timedelta

import numpy as np

from app.services.correlation import DependencyGraph, correlate
from app.services.detection import (DetectedAnomaly, detect_signatures, detect_statistical, prepare_frame,
                                    robust_zscores)
from app.services.signatures import default_matcher

T0 = datetime(2024, 5, 1, 12, 0, 0)


def test_robust_z_mad():
    st = robust_zscores([10, 11, 9, 10, 12, 10, 50])
    assert st.method == "mad" and st.median == 10
    assert st.z[-1] > 10 and abs(st.z[0]) < 1


def test_robust_z_zero_mad_fallback():
    st = robust_zscores([0, 0, 0, 0, 0, 0, 0, 20])
    assert st.mad == 0 and st.method == "meanad"
    assert st.z[-1] > 3.5


def test_robust_z_constant_series():
    st = robust_zscores([5, 5, 5])
    assert st.method == "degenerate" and np.all(st.z == 0)


def _rows(spike_minute=None, spike_errors=0, minutes=30, svc="api"):
    rows, i = [], 0
    for m in range(minutes):
        for k in range(10):
            i += 1
            rows.append({"id": i, "timestamp": T0 + timedelta(minutes=m, seconds=k * 5), "service": svc,
                         "severity": "INFO", "message": f"GET /x 200 in {40 + k}ms", "trace_id": f"t{i}",
                         "metadata": {"latency_ms": 40 + k}})
        if m == spike_minute:
            for k in range(spike_errors):
                i += 1
                rows.append({"id": i, "timestamp": T0 + timedelta(minutes=m, seconds=k), "service": svc,
                             "severity": "ERROR", "message": "Database query timed out after 5000ms",
                             "trace_id": f"e{i}", "metadata": {"latency_ms": 5000}})
    return rows


def test_statistical_detects_error_spike_with_evidence():
    df = prepare_frame(_rows(spike_minute=20, spike_errors=15), default_matcher())
    found = detect_statistical(df, window_seconds=60, z_threshold=3.5, min_count=3)
    err = [a for a in found if a.metric == "error_count"]
    assert len(err) == 1
    a = err[0]
    assert a.window_start == T0 + timedelta(minutes=20) and a.observed_value == 15
    assert a.evidence_count == 15 and len(a.evidence_log_ids) == 15
    assert any(x.metric == "latency_p95_ms" for x in found)


def test_statistical_quiet_on_normal_traffic():
    df = prepare_frame(_rows(), default_matcher())
    assert detect_statistical(df, 60, 3.5, 3) == []


def test_statistical_needs_baseline_windows():
    df = prepare_frame(_rows(spike_minute=2, spike_errors=15, minutes=4), default_matcher())
    assert detect_statistical(df, 60, 3.5, 3, min_windows=8) == []


def test_signature_detection_respects_min_count():
    df = prepare_frame(_rows(spike_minute=20, spike_errors=15), default_matcher())
    sig = detect_signatures(df, default_matcher(), 60)
    keys = {a.signature_key for a in sig}
    assert keys == {"db_timeout"}
    assert sig[0].evidence_count == 15 and sig[0].category == "database"

    df1 = prepare_frame(_rows(spike_minute=20, spike_errors=1), default_matcher())
    assert detect_signatures(df1, default_matcher(), 60) == []  # db_timeout min_count=2


def test_matcher_examples():
    m = default_matcher()
    assert "out_of_memory" in m.match("java.lang.OutOfMemoryError: Java heap space")
    assert "connection_refused" in m.match("dial tcp 10.0.0.1:80: connection refused")
    assert "http_5xx" in m.match("POST /api -> 503 Service Unavailable")
    assert m.match("JVM heap usage 55% (GC young pause 12ms)") == []
    assert m.match("GET /api/orders/429 -> 200 OK") == []


def _anom(service, minute, severity="high", traces=(), dur=2):
    return DetectedAnomaly(kind="signature", detector="x", metric="m", service=service,
                           window_start=T0 + timedelta(minutes=minute),
                           window_end=T0 + timedelta(minutes=minute + dur), observed_value=1,
                           severity=severity, score=0.7, description="", trace_ids=list(traces))


def test_correlation_groups_by_time_service_trace_and_dependency():
    graph = DependencyGraph({("gateway", "orders"): 10, ("orders", "db"): 10})
    anoms = [
        _anom("db", 0), _anom("orders", 1), _anom("gateway", 2),       # dependency chain -> one incident
        _anom("auth", 3, traces=["t1"]), _anom("billing", 4, traces=["t1"]),  # shared trace -> another
        _anom("db", 120),                                              # far away -> separate incident
    ]
    groups, leftovers = correlate(anoms, graph, gap_seconds=300)
    svc_sets = sorted(tuple(g.services) for g in groups)
    assert svc_sets == [("auth", "billing"), ("db",), ("db", "gateway", "orders")]
    assert leftovers == []


def test_low_severity_never_bridges_groups():
    graph = DependencyGraph()
    anoms = [_anom("a", 0), _anom("a", 6, severity="low", dur=10), _anom("a", 14)]
    groups, _ = correlate(anoms, graph, gap_seconds=60)
    assert len(groups) == 2


def test_low_singletons_are_leftovers():
    groups, leftovers = correlate([_anom("a", 0, severity="medium")], DependencyGraph(), 300)
    assert groups == [] and len(leftovers) == 1


def test_dependency_graph_from_traces():
    import pandas as pd

    rows = []
    for t in range(3):
        rows += [{"trace_id": f"t{t}", "service": "gw", "timestamp": T0},
                 {"trace_id": f"t{t}", "service": "orders", "timestamp": T0 + timedelta(milliseconds=5)},
                 {"trace_id": f"t{t}", "service": "db", "timestamp": T0 + timedelta(milliseconds=9)}]
    g = DependencyGraph.from_frame(pd.DataFrame(rows))
    assert g.callees("gw") == {"orders"} and g.callees("orders") == {"db"}
    assert g.upstream_of("db") == {"gw", "orders"}
