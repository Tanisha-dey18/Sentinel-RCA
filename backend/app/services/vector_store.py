"""Incident similarity search backed by Qdrant (server or embedded) with a NumPy fallback."""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

import numpy as np

from app.config import get_settings
from app.services.embeddings import get_embedder, incident_text

log = logging.getLogger(__name__)


class _NumpyIndex:
    """In-process brute-force cosine index used when qdrant-client is unavailable."""

    backend = "numpy"

    def __init__(self) -> None:
        self.vectors: dict[int, np.ndarray] = {}
        self.payloads: dict[int, dict] = {}

    def upsert(self, pid: int, vector: np.ndarray, payload: dict) -> None:
        self.vectors[pid] = vector
        self.payloads[pid] = payload

    def delete(self, ids: list[int]) -> None:
        for i in ids:
            self.vectors.pop(i, None)
            self.payloads.pop(i, None)

    def search(self, vector: np.ndarray, limit: int, exclude: int | None) -> list[tuple[int, float, dict]]:
        scored = [(pid, float(np.dot(vector, v)), self.payloads[pid]) for pid, v in self.vectors.items()
                  if pid != exclude]
        return sorted(scored, key=lambda t: -t[1])[:limit]

    def count(self) -> int:
        return len(self.vectors)


class _QdrantIndex:
    def __init__(self, dim: int):
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm

        s = get_settings()
        self.qm = qm
        if s.qdrant_url:
            self.client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None, timeout=5)
            self.backend = f"qdrant:{s.qdrant_url}"
        elif s.qdrant_path == ":memory:":
            self.client = QdrantClient(location=":memory:")
            self.backend = "qdrant:embedded-memory"
        else:
            os.makedirs(s.qdrant_path, exist_ok=True)
            self.client = QdrantClient(path=s.qdrant_path)
            self.backend = f"qdrant:embedded:{s.qdrant_path}"
        self.collection = s.qdrant_collection
        if self.client.collection_exists(self.collection):
            info = self.client.get_collection(self.collection)
            size = info.config.params.vectors.size  # type: ignore[union-attr]
            if size != dim:
                log.warning("Recreating Qdrant collection: dimension changed %s -> %s", size, dim)
                self.client.delete_collection(self.collection)
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=qm.VectorParams(
                size=dim, distance=qm.Distance.COSINE))

    def upsert(self, pid: int, vector: np.ndarray, payload: dict) -> None:
        self.client.upsert(self.collection, points=[
            self.qm.PointStruct(id=pid, vector=vector.tolist(), payload=payload)])

    def delete(self, ids: list[int]) -> None:
        if ids:
            self.client.delete(self.collection, points_selector=self.qm.PointIdsList(points=ids))

    def search(self, vector: np.ndarray, limit: int, exclude: int | None) -> list[tuple[int, float, dict]]:
        flt = None
        if exclude is not None:
            flt = self.qm.Filter(must_not=[self.qm.HasIdCondition(has_id=[exclude])])
        res = self.client.query_points(self.collection, query=vector.tolist(), limit=limit, query_filter=flt,
                                       with_payload=True)
        return [(int(p.id), float(p.score), p.payload or {}) for p in res.points]

    def count(self) -> int:
        return int(self.client.count(self.collection).count)


class IncidentVectorStore:
    def __init__(self) -> None:
        self.embedder = get_embedder()
        try:
            self.index: Any = _QdrantIndex(self.embedder.dim)
        except Exception as exc:
            log.warning("Qdrant unavailable (%s); using in-process NumPy index", exc)
            self.index = _NumpyIndex()

    @property
    def backend(self) -> str:
        return self.index.backend

    def upsert_incident(self, incident: dict) -> None:
        vec = self.embedder.embed([incident_text(incident)])[0]
        payload = {k: incident.get(k) for k in ("id", "title", "severity", "status", "services", "categories")}
        payload["started_at"] = str(incident.get("started_at"))
        self.index.upsert(int(incident["id"]), vec, payload)

    def delete(self, ids: list[int]) -> None:
        try:
            self.index.delete(ids)
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("vector delete failed: %s", exc)

    def similar(self, incident: dict, limit: int = 5) -> list[tuple[int, float]]:
        vec = self.embedder.embed([incident_text(incident)])[0]
        return [(pid, score) for pid, score, _ in self.index.search(vec, limit, exclude=int(incident["id"]))]

    def count(self) -> int:
        try:
            return self.index.count()
        except Exception:
            return -1


_store: IncidentVectorStore | None = None
_lock = threading.Lock()


def get_vector_store() -> IncidentVectorStore:
    global _store
    with _lock:
        if _store is None:
            _store = IncidentVectorStore()
        return _store
