"""On-disk cache for LLM responses and downloaded files.

The Silent Change Detector must be cheap and fast on reruns, so every LLM call
is cached to a JSON file keyed by a stable hash of the request. Cache files are
written atomically so an interrupted run never leaves a half-written entry.

Layout::

    cache/llm/<sha256>.json     # cached LLM responses
    cache/files/<sha256>.<ext>  # cached downloaded binary files (e.g. PDFs)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import config


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _stable_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# JSON cache (for LLM responses and other small structured results)
# ---------------------------------------------------------------------------
def cache_key(*parts: Any, namespace: str = "llm") -> Path:
    """Return the cache file path for the given key ``parts``."""
    joined = "||".join(str(p) for p in parts)
    digest = _stable_hash(f"{namespace}::{joined}")
    return config.CACHE_DIR / namespace / f"{digest}.json"


def get_json(*parts: Any, namespace: str = "llm") -> Any | None:
    """Return a cached JSON value for ``parts`` or ``None`` on a miss."""
    path = cache_key(*parts, namespace=namespace)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        # Corrupt cache entry: treat as a miss so the caller recomputes.
        return None


def set_json(value: Any, *parts: Any, namespace: str = "llm") -> Path:
    """Persist ``value`` as JSON for ``parts`` and return the cache path."""
    path = cache_key(*parts, namespace=namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(path, payload)
    return path


def has_json(*parts: Any, namespace: str = "llm") -> bool:
    return cache_key(*parts, namespace=namespace).exists()


# ---------------------------------------------------------------------------
# File cache (for downloaded binary files such as PDFs)
# ---------------------------------------------------------------------------
def cache_file(url: str, ext: str = "bin") -> Path:
    """Return the cache path for a downloaded file addressed by ``url``."""
    digest = _stable_hash(f"file::{url}")
    ext = ext.lstrip(".") or "bin"
    return config.CACHE_DIR / "files" / f"{digest}.{ext}"


def get_file(url: str, ext: str = "bin") -> bytes | None:
    path = cache_file(url, ext)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def set_file(data: bytes, url: str, ext: str = "bin") -> Path:
    path = cache_file(url, ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(path, data)
    return path
