"""Service dependency inference and anomaly -> incident correlation."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd

from app.services.detection import SEVERITY_LEVELS, DetectedAnomaly, bump


@dataclass
class DependencyGraph:
    """Directed call graph inferred from traces: an edge a -> b means, within a shared trace,
    service `a` logged before service `b` (a calls b)."""

    edges: dict[tuple[str, str], int] = field(default_factory=dict)

    @classmethod
    def from_frame(cls, df: pd.DataFrame, min_support: int = 2) -> DependencyGraph:
        if df.empty or "trace_id" not in df:
            return cls()
        t = df.dropna(subset=["trace_id"])[["trace_id", "service", "timestamp"]]
        if t.empty:
            return cls()
        firsts = (t.sort_values(["trace_id", "timestamp"], kind="stable")
                   .drop_duplicates(["trace_id", "service"], keep="first"))
        firsts = firsts.assign(prev=firsts.groupby("trace_id")["service"].shift(1)).dropna(subset=["prev"])
        counts = firsts.groupby(["prev", "service"]).size()
        edges = {(str(a), str(b)): int(c) for (a, b), c in counts.items() if c >= min_support and a != b}
        return cls(edges)

    def callees(self, s: str) -> set[str]:
        return {b for (a, b) in self.edges if a == s}

    def callers(self, s: str) -> set[str]:
        return {a for (a, b) in self.edges if b == s}

    def neighbors(self, s: str) -> set[str]:
        return self.callees(s) | self.callers(s)

    def upstream_of(self, s: str) -> set[str]:
        """All services that (transitively) call `s`."""
        seen: set[str] = set()
        stack = [s]
        while stack:
            for c in self.callers(stack.pop()):
                if c not in seen and c != s:
                    seen.add(c)
                    stack.append(c)
        return seen

    def to_dict(self) -> dict:
        nodes = sorted({n for e in self.edges for n in e})
        return {"nodes": nodes, "edges": [{"source": a, "target": b, "count": c} for (a, b), c in
                                           sorted(self.edges.items())]}

    @classmethod
    def from_dict(cls, d: dict | None) -> DependencyGraph:
        if not d:
            return cls()
        return cls({(e["source"], e["target"]): int(e["count"]) for e in d.get("edges", [])})


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


@dataclass
class CorrelatedGroup:
    anomalies: list[DetectedAnomaly]
    links: list[dict]  # why anomalies were linked (evidence for the grouping itself)

    @property
    def services(self) -> list[str]:
        return sorted({a.service for a in self.anomalies})

    @property
    def start(self):
        return min(a.window_start for a in self.anomalies)

    @property
    def end(self):
        return max(a.window_end for a in self.anomalies)

    @property
    def severity(self) -> str:
        sev = max((a.severity for a in self.anomalies), key=SEVERITY_LEVELS.index)
        if len(self.services) >= 3:
            sev = bump(sev, 1)
        return sev


def correlate(anomalies: list[DetectedAnomaly], graph: DependencyGraph, gap_seconds: int = 300,
              min_singleton_severity: str = "high") -> tuple[list[CorrelatedGroup], list[DetectedAnomaly]]:
    """Group anomalies into incidents.

    Two anomalies are linked when their time windows are within `gap_seconds` of each other AND
    they share a service, share a trace ID, or their services are adjacent in the dependency graph.
    Returns (incident groups, uncorrelated low-signal anomalies).
    """
    if not anomalies:
        return [], []
    order = sorted(range(len(anomalies)), key=lambda i: anomalies[i].window_start)
    items = [anomalies[i] for i in order]
    uf = _UnionFind(len(items))
    gap = timedelta(seconds=gap_seconds)
    links: list[tuple[int, int, str]] = []
    trace_sets = [set(a.trace_ids) for a in items]

    def link_reason(i: int, j: int) -> str | None:
        a, b = items[i], items[j]
        if b.window_start > a.window_end + gap or a.window_start > b.window_end + gap:
            return None
        if a.service == b.service:
            return "same_service"
        if trace_sets[i] & trace_sets[j]:
            return "shared_trace"
        if b.service in graph.neighbors(a.service):
            return "dependency"
        return None

    # Pass 1: link significant (medium+) anomalies transitively.
    weak = [items[k].severity == "low" for k in range(len(items))]
    for i in range(len(items)):
        if weak[i]:
            continue
        for j in range(i + 1, len(items)):
            if items[j].window_start > items[i].window_end + gap:
                break  # sorted by start; nothing further can be within the gap
            if weak[j]:
                continue
            if reason := link_reason(i, j):
                uf.union(i, j)
                links.append((i, j, reason))
    # Pass 2: low-severity anomalies attach to (at most) one group but never bridge two groups,
    # so background noise cannot chain unrelated incidents together.
    for i in range(len(items)):
        if not weak[i]:
            continue
        candidates = [(j, r) for j in range(len(items)) if not weak[j] and (r := link_reason(min(i, j), max(i, j)))]
        if candidates:
            j, reason = min(candidates, key=lambda c: abs((items[c[0]].window_start - items[i].window_start)
                                                          .total_seconds()))
            uf.union(i, j)
            links.append((min(i, j), max(i, j), reason))

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(items)):
        groups[uf.find(i)].append(i)
    link_by_root: dict[int, list[dict]] = defaultdict(list)
    for i, j, reason in links:
        link_by_root[uf.find(i)].append({"a": i, "b": j, "reason": reason})

    min_rank = SEVERITY_LEVELS.index(min_singleton_severity)
    result: list[CorrelatedGroup] = []
    leftovers: list[DetectedAnomaly] = []
    for root, members in groups.items():
        group_anoms = [items[k] for k in members]
        if len(group_anoms) == 1 and SEVERITY_LEVELS.index(group_anoms[0].severity) < min_rank:
            leftovers.extend(group_anoms)
            continue
        index_map = {k: n for n, k in enumerate(members)}
        result.append(CorrelatedGroup(
            anomalies=group_anoms,
            links=[{"a": index_map[l["a"]], "b": index_map[l["b"]], "reason": l["reason"]}
                   for l in link_by_root[root]],
        ))
    result.sort(key=lambda g: g.start)
    return result, leftovers
