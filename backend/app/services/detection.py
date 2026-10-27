"""Anomaly detection: robust z-scores (MAD) over windowed metrics plus signature bursts.

Robust z-score (Iglewicz & Hoaglin):   z = 0.6745 * (x - median) / MAD
When MAD == 0 (very common for sparse error counts) we fall back to the scaled mean
absolute deviation:                   z = (x - median) / (1.253314 * MeanAD)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.services.signatures import SignatureMatcher

SEVERITY_LEVELS = ("low", "medium", "high", "critical")
SEVERITY_WEIGHT = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
ERROR_SEVERITIES = ("ERROR", "CRITICAL")
LATENCY_KEYS = ("latency_ms", "duration_ms", "response_time_ms", "elapsed_ms", "latency")


@dataclass
class DetectedAnomaly:
    kind: str  # "statistical" | "signature"
    detector: str
    metric: str
    service: str
    window_start: datetime
    window_end: datetime
    observed_value: float
    severity: str
    score: float
    description: str
    baseline_median: float | None = None
    mad: float | None = None
    robust_z: float | None = None
    signature_key: str | None = None
    category: str | None = None
    evidence_log_ids: list[int] = field(default_factory=list)
    evidence_count: int = 0
    trace_ids: list[str] = field(default_factory=list)
    id: int | None = None  # assigned after persistence


@dataclass
class RobustStats:
    z: np.ndarray
    median: float
    mad: float
    method: str  # "mad" | "meanad" | "degenerate"


def robust_zscores(values: np.ndarray | list[float]) -> RobustStats:
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        return RobustStats(np.array([]), 0.0, 0.0, "degenerate")
    median = float(np.median(x))
    abs_dev = np.abs(x - median)
    mad = float(np.median(abs_dev))
    if mad > 0:
        return RobustStats(0.6745 * (x - median) / mad, median, mad, "mad")
    mean_ad = float(np.mean(abs_dev))
    if mean_ad > 0:
        return RobustStats((x - median) / (1.253314 * mean_ad), median, mad, "meanad")
    return RobustStats(np.zeros_like(x), median, mad, "degenerate")


def severity_from_z(z: float) -> str:
    az = abs(z)
    if az >= 12:
        return "critical"
    if az >= 7:
        return "high"
    if az >= 3.5:
        return "medium"
    return "low"


def bump(severity: str, delta: int) -> str:
    i = SEVERITY_LEVELS.index(severity) if severity in SEVERITY_LEVELS else 1
    return SEVERITY_LEVELS[max(0, min(len(SEVERITY_LEVELS) - 1, i + delta))]


def _runs(indices: list[int], max_gap: int = 1) -> list[list[int]]:
    """Group sorted bucket indices into runs, tolerating `max_gap - 1` empty buckets."""
    runs: list[list[int]] = []
    for i in sorted(indices):
        if runs and i - runs[-1][-1] <= max_gap:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def extract_latency(metadata: dict | None) -> float:
    if not metadata:
        return np.nan
    for k in LATENCY_KEYS:
        v = metadata.get(k)
        if v is None:
            continue
        try:
            return float(str(v).rstrip("ms").strip()) if isinstance(v, str) else float(v)
        except ValueError:
            continue
    return np.nan


def prepare_frame(rows: list[dict], matcher: SignatureMatcher) -> pd.DataFrame:
    """Build the analysis dataframe. Each row: id, timestamp, service, severity, message, trace_id, metadata."""
    cols = ["id", "timestamp", "service", "severity", "message", "trace_id", "latency_ms", "sigs"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["latency_ms"] = [extract_latency(m) for m in df.get("metadata", [None] * len(df))]
    df["sigs"] = [matcher.match(m) for m in df["message"]]
    df = df.drop(columns=[c for c in ("metadata",) if c in df.columns])
    return df.sort_values("timestamp", kind="stable").reset_index(drop=True)


def _bucketize(df: pd.DataFrame, window_seconds: int) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    df = df.copy()
    freq = f"{int(window_seconds)}s"
    df["bucket"] = df["timestamp"].dt.floor(freq)
    full_index = pd.date_range(df["bucket"].min(), df["bucket"].max(), freq=freq)
    return df, full_index


def _evidence(g: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, mask: pd.Series | None,
              max_evidence: int, sort_by: str | None = None) -> tuple[list[int], int, list[str]]:
    sel = g[(g["timestamp"] >= start) & (g["timestamp"] < end)]
    if mask is not None:
        sel = sel[mask.reindex(sel.index, fill_value=False)]
    if sort_by:
        sel = sel.sort_values(sort_by, ascending=False)
    ids = [int(i) for i in sel["id"].head(max_evidence)]
    traces = [str(t) for t in sel["trace_id"].dropna().unique()[:100]]
    return ids, int(len(sel)), traces


def detect_statistical(df: pd.DataFrame, window_seconds: int = 60, z_threshold: float = 3.5,
                       min_count: int = 3, min_windows: int = 8, max_evidence: int = 50) -> list[DetectedAnomaly]:
    """Detect anomalous windows per service for error counts, p95 latency and log-volume drops."""
    if df.empty:
        return []
    df, full_index = _bucketize(df, window_seconds)
    if len(full_index) < min_windows:
        return []
    win = pd.Timedelta(seconds=window_seconds)
    df["is_error"] = df["severity"].isin(ERROR_SEVERITIES)
    out: list[DetectedAnomaly] = []

    for service, g in df.groupby("service", sort=True):
        agg = g.groupby("bucket").agg(volume=("id", "size"), errors=("is_error", "sum")).reindex(full_index, fill_value=0)

        # 1) Error-count spikes (upward only).
        errors = agg["errors"].to_numpy(float)
        st = robust_zscores(errors)
        flagged = [i for i in range(len(errors)) if st.z[i] >= z_threshold and errors[i] >= min_count]
        for run in _runs(flagged):
            peak = max(run, key=lambda i: errors[i])
            start, end = full_index[run[0]], full_index[run[-1]] + win
            ids, n, traces = _evidence(g, start, end, g["is_error"], max_evidence)
            z = float(st.z[peak])
            out.append(DetectedAnomaly(
                kind="statistical", detector="robust_z", metric="error_count", service=str(service),
                window_start=start.to_pydatetime(), window_end=end.to_pydatetime(),
                observed_value=float(errors[peak]), baseline_median=st.median, mad=st.mad, robust_z=round(z, 2),
                severity=severity_from_z(z), score=round(min(1.0, abs(z) / 15), 3),
                description=(f"{service}: {int(errors[peak])} error logs in one {window_seconds}s window "
                             f"vs. baseline median {st.median:g} (MAD {st.mad:g}, robust z={z:.1f}, "
                             f"{len(run)} window(s), {n} error logs total)."),
                evidence_log_ids=ids, evidence_count=n, trace_ids=traces,
            ))

        # 2) p95 latency spikes (upward only), where latency metadata exists.
        lat = g.dropna(subset=["latency_ms"])
        if len(lat) >= min_windows:
            p95 = lat.groupby("bucket")["latency_ms"].quantile(0.95).reindex(full_index)
            valid = ~p95.isna()
            if valid.sum() >= min_windows:
                vals = p95[valid].to_numpy(float)
                pos = np.flatnonzero(valid.to_numpy())
                st = robust_zscores(vals)
                flagged_pos = [int(pos[k]) for k in range(len(vals))
                               if st.z[k] >= z_threshold and vals[k] >= 1.5 * max(st.median, 1.0)]
                z_by_pos = {int(pos[k]): float(st.z[k]) for k in range(len(vals))}
                for run in _runs(flagged_pos, max_gap=2):
                    peak = max(run, key=lambda i: p95.iloc[i])
                    start, end = full_index[run[0]], full_index[run[-1]] + win
                    slow = g["latency_ms"] >= 1.5 * max(st.median, 1.0)
                    ids, n, traces = _evidence(g, start, end, slow, max_evidence, sort_by="latency_ms")
                    z = z_by_pos[peak]
                    out.append(DetectedAnomaly(
                        kind="statistical", detector="robust_z", metric="latency_p95_ms", service=str(service),
                        window_start=start.to_pydatetime(), window_end=end.to_pydatetime(),
                        observed_value=round(float(p95.iloc[peak]), 1), baseline_median=round(st.median, 1),
                        mad=round(st.mad, 2), robust_z=round(z, 2), severity=severity_from_z(z),
                        score=round(min(1.0, abs(z) / 15), 3),
                        description=(f"{service}: p95 latency {p95.iloc[peak]:.0f} ms vs. baseline median "
                                     f"{st.median:.0f} ms (robust z={z:.1f}, {n} slow requests)."),
                        evidence_log_ids=ids, evidence_count=n, trace_ids=traces,
                    ))

        # 3) Log-volume drops (service went quiet: crash, network partition). Edge windows are
        #    excluded because they are usually partial.
        vol = agg["volume"].to_numpy(float)
        st = robust_zscores(vol)
        if st.median >= 5:
            flagged = [i for i in range(1, len(vol) - 1)
                       if st.z[i] <= -z_threshold and vol[i] <= 0.5 * st.median]
            for run in _runs(flagged):
                trough = min(run, key=lambda i: vol[i])
                start, end = full_index[run[0]], full_index[run[-1]] + win
                ids, n, traces = _evidence(g, start, end, None, max_evidence)
                z = float(st.z[trough])
                out.append(DetectedAnomaly(
                    kind="statistical", detector="robust_z", metric="log_volume_drop", service=str(service),
                    window_start=start.to_pydatetime(), window_end=end.to_pydatetime(),
                    observed_value=float(vol[trough]), baseline_median=st.median, mad=st.mad, robust_z=round(z, 2),
                    severity=severity_from_z(z), score=round(min(1.0, abs(z) / 15), 3),
                    description=(f"{service}: log volume fell to {int(vol[trough])} per {window_seconds}s "
                                 f"vs. baseline median {st.median:g} (robust z={z:.1f}) — the service may be "
                                 f"down or unreachable."),
                    evidence_log_ids=ids, evidence_count=n, trace_ids=traces,
                ))
    return out


def detect_signatures(df: pd.DataFrame, matcher: SignatureMatcher, window_seconds: int = 60,
                      z_threshold: float = 3.5, max_evidence: int = 50) -> list[DetectedAnomaly]:
    """Detect bursts of signature matches per (signature, service), merged across consecutive windows."""
    if df.empty or "sigs" not in df:
        return []
    df, full_index = _bucketize(df, window_seconds)
    exploded = df[["id", "bucket", "service", "sigs"]].explode("sigs").dropna(subset=["sigs"])
    if exploded.empty:
        return []
    win = pd.Timedelta(seconds=window_seconds)
    pos_of = {ts: i for i, ts in enumerate(full_index)}
    out: list[DetectedAnomaly] = []

    for (key, service), g in exploded.groupby(["sigs", "service"], sort=True):
        sig = matcher.get(key)
        if sig is None:
            continue
        counts = g.groupby("bucket").size().reindex(full_index, fill_value=0)
        vals = counts.to_numpy(float)
        st = robust_zscores(vals) if len(vals) >= 3 else None
        flagged = [pos_of[b] for b, c in counts.items() if c >= sig.min_count]
        if not flagged:
            continue
        svc_logs = df[df["service"] == service]
        sig_mask = svc_logs["sigs"].apply(lambda s, k=key: k in s)
        for run in _runs(flagged, max_gap=2):
            peak = max(run, key=lambda i: vals[i])
            start, end = full_index[run[0]], full_index[run[-1]] + win
            ids, n, traces = _evidence(svc_logs, start, end, sig_mask, max_evidence)
            z = float(st.z[peak]) if st is not None else None
            severity = sig.severity
            note = ""
            # A signature that fires at a steady, normal rate is downgraded: it is evidence, not a spike.
            if z is not None and z < z_threshold and severity != "critical" and st.median > 0:
                severity = bump(severity, -1)
                note = " Rate is within this service's normal baseline."
            out.append(DetectedAnomaly(
                kind="signature", detector=f"signature:{key}", metric="signature_hits", service=str(service),
                window_start=start.to_pydatetime(), window_end=end.to_pydatetime(),
                observed_value=float(vals[peak]),
                baseline_median=st.median if st else None, mad=st.mad if st else None,
                robust_z=round(z, 2) if z is not None else None,
                signature_key=key, category=sig.category, severity=severity,
                score=round(SEVERITY_WEIGHT[severity] * min(1.0, 0.6 + n / 50), 3),
                description=(f"{service}: {n} log(s) matched signature '{sig.name}' between "
                             f"{start:%H:%M:%S} and {end:%H:%M:%S} (peak {int(vals[peak])} per "
                             f"{window_seconds}s, threshold {sig.min_count}).{note}"),
                evidence_log_ids=ids, evidence_count=n, trace_ids=traces,
            ))
    return out


def window_of(ts: datetime, window_seconds: int) -> datetime:
    epoch = datetime(1970, 1, 1)
    secs = int((ts - epoch).total_seconds()) // window_seconds * window_seconds
    return epoch + timedelta(seconds=secs)
