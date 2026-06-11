"""Prompt embeddings + cosine similarity for the answer cache and the
category→template recognition layer.

A thin, dependency-light wrapper over an OpenAI-compatible
``/v1/embeddings`` endpoint (default model ``text-embedding-3-small``,
1536-d, ~$0.00002/query).  Results are cached in-process by a SHA-256 of
the input text, so re-embedding the same prompt (e.g. the recognition
layer and the indexer both embedding one turn's prompt) costs a single
network call.

Design notes:
  * No hard dependency on the ``openai`` SDK — uses ``httpx`` the same way
    ``studio/express.py`` talks to ``/chat/completions``.
  * Degrades gracefully: if no API key is configured, ``embed`` returns
    ``None`` so callers can treat the cache as simply disabled rather than
    erroring.
  * Pure-Python cosine (no numpy dependency at import time); callers that
    need a bulk scan can build their own matrix.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
from collections import OrderedDict
from typing import Optional

_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_DIM = 1536

# Bounded in-process cache: text-hash -> vector.  ~1536 floats * 8 bytes
# ≈ 12 KB/entry; 4096 entries ≈ 50 MB worst case.
_CACHE_MAX = int(os.environ.get("SEVIM_EMBED_CACHE_MAX", "4096"))
_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_cache_lock = threading.Lock()


def _key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()


def _cache_get(k: str) -> Optional[list[float]]:
    with _cache_lock:
        v = _cache.get(k)
        if v is not None:
            _cache.move_to_end(k)
        return v


def _cache_put(k: str, v: list[float]) -> None:
    with _cache_lock:
        _cache[k] = v
        _cache.move_to_end(k)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def _api_key() -> str:
    return (os.environ.get("SEVIM_EMBED_API_KEY")
            or os.environ.get("OPENAI_API_KEY") or "")


def _base_url() -> str:
    return (os.environ.get("SEVIM_EMBED_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1").rstrip("/")


def _model() -> str:
    return os.environ.get("SEVIM_EMBED_MODEL", _DEFAULT_MODEL)


def embed(text: str, *, model: Optional[str] = None,
          timeout_s: float = 15.0) -> Optional[list[float]]:
    """Return the embedding vector for ``text``, or None if embeddings are
    unavailable (no key, network/HTTP error).  Cached by text hash."""
    text = (text or "").strip()
    if not text:
        return None
    mdl = model or _model()
    k = _key(text, mdl)
    hit = _cache_get(k)
    if hit is not None:
        return hit
    key = _api_key()
    if not key:
        return None
    try:
        import httpx
        with httpx.Client(timeout=timeout_s) as c:
            r = c.post(
                f"{_base_url()}/embeddings",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"model": mdl, "input": text[:8000]},
            )
        if r.status_code != 200:
            return None
        vec = r.json()["data"][0]["embedding"]
        vec = [float(x) for x in vec]
    except Exception:  # noqa: BLE001 — embeddings are best-effort
        return None
    _cache_put(k, vec)
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors.  0.0 on degenerate
    input (empty or zero-norm) so callers never divide by zero."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def available() -> bool:
    """True iff an embedding API key is configured."""
    return bool(_api_key())
