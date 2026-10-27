"""Configurable text embedding providers.

* ``sentence-transformers``: semantic embeddings (requires the optional `sentence-transformers` package).
* ``hash``: dependency-free feature hashing of word uni/bi-grams + char trigrams (deterministic, offline).
* ``auto``: sentence-transformers when importable, otherwise hash.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Protocol

import numpy as np

from app.config import get_settings

log = logging.getLogger(__name__)
_TOKEN = re.compile(r"[a-z][a-z0-9_]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    def __init__(self, dim: int = 384):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _bucket(self, token: str) -> tuple[int, float]:
        h = hashlib.blake2b(token.encode(), digest_size=8).digest()
        v = int.from_bytes(h, "little")
        return v % self.dim, 1.0 if (v >> 63) & 1 else -1.0

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            words = _TOKEN.findall(text.lower())
            feats: list[tuple[str, float]] = [(w, 1.0) for w in words]
            feats += [(f"{a}_{b}", 0.7) for a, b in zip(words, words[1:])]
            for w in words:
                feats += [(f"#{w[i:i + 3]}", 0.3) for i in range(max(0, len(w) - 2))]
            for tok, weight in feats:
                idx, sign = self._bucket(tok)
                out[row, idx] += sign * weight
            norm = np.linalg.norm(out[row])
            if norm > 0:
                out[row] /= norm
        return out


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model = SentenceTransformer(model_name)
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.name = f"st:{model_name}"

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)


_lock = threading.Lock()
_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    with _lock:
        if _embedder is None:
            s = get_settings()
            provider = s.embedding_provider.lower()
            if provider in ("sentence-transformers", "auto"):
                try:
                    _embedder = SentenceTransformerEmbedder(s.embedding_model)
                except Exception as exc:  # ImportError or model download failure
                    if provider != "auto":
                        log.warning("sentence-transformers unavailable (%s); falling back to hash embeddings", exc)
                    _embedder = HashEmbedder(s.embedding_dim)
            else:
                _embedder = HashEmbedder(s.embedding_dim)
            log.info("Embedding provider: %s", _embedder.name)
        return _embedder


def incident_text(incident: dict) -> str:
    rc = incident.get("root_cause") or {}
    primary = rc.get("primary") or {}
    parts = [
        incident.get("title", ""),
        "services: " + " ".join(incident.get("services", [])),
        "categories: " + " ".join(incident.get("categories", [])),
        "signatures: " + " ".join(s.get("key", "") for s in rc.get("signatures", [])),
        "root cause: " + primary.get("title", "") + ". " + primary.get("explanation", ""),
        "chain: " + " ".join(f"{c['cause']} {c['effect']}" for c in rc.get("causal_chain", [])),
        " ".join((a.get("first_error_message") or "") for a in rc.get("affected_services", [])[:5]),
    ]
    return "\n".join(p for p in parts if p)
