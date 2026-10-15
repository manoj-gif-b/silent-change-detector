"""Robust LLM access layer (Gemini via google-genai) with an offline mode.

This is the ONLY module in the project that talks to the LLM. Every other
module (ledger, detectors, hypothesis, redteam, run.py, app.py) imports
``get_llm()`` from here and calls ``complete_text`` / ``complete_json``.

Features (per project spec):

* Loads ``GEMINI_API_KEY`` from ``.env``; the key is stripped of spaces and
  quotes. A missing key raises a clear, actionable error.
* Model selection reads ``GEMINI_MODELS`` (a comma-separated fallback list,
  e.g. ``model-a,model-b,model-c``). Each model is tried with up to 3 attempts;
  if it keeps failing with 503/504, 429, or an SSL/connection error we
  automatically fall back to the next model in the list. ``GEMINI_MODEL`` still
  works for a single model (backward compatible).
* Calls Gemini through the official ``google-genai`` package.
* Retries transient failures (ECONNRESET, timeouts, HTTP 429 and 5xx) with
  exponential backoff + random jitter: 3s -> 60s between attempts.
* Per-request timeout defaults to 120s (``SCD_LLM_TIMEOUT_SECONDS``;
  ``HttpOptions.timeout`` is in milliseconds).
* Prints which model actually answered each call, so fallback is visible.
* Every response is cached on disk by SHA-256 of (task, system, prompt) -- NOT
  by model -- via :mod:`scd.cache`, so a result from ANY model is reused and an
  interrupted run resumes without repeating a call.
* Failures are classified into short, readable kinds: ``bad key``,
  ``model not found``, ``rate limit``, ``network``, ``server`` (Google-side
  5xx) or ``unknown``. The API key is NEVER logged or printed
  (see :func:`_redact`).
* Honours ``HTTPS_PROXY`` / ``HTTP_PROXY`` environment variables (the SDK's
  underlying httpx client reads them automatically) and prints whether a proxy
  is in use.
* Optional offline mode: with ``LLM_MODE=offline`` in ``.env``, uses the demo
  scripted LLM so the whole pipeline runs without internet or an API key.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from typing import Any

from dotenv import load_dotenv

from . import cache
from .config import LLM_TIMEOUT_SECONDS

logger = logging.getLogger("scd.llm")

# Load .env early (idempotent; existing env vars win, matching config.py).
load_dotenv()

DEFAULT_MODEL = "gemini-2.5-flash"
# Per-request network timeout. Configurable via SCD_LLM_TIMEOUT_SECONDS
# (default 120s per spec; floor 30s) so a slow 504 + retry has room to finish.
REQUEST_TIMEOUT_MS = int(LLM_TIMEOUT_SECONDS * 1000)

# Multi-model fallback (GEMINI_MODELS = comma-separated list). Each model is
# tried this many times before we fall back to the next model in the list.
MODEL_ATTEMPTS = 3
# Total attempts ceiling (safety cap if only one model is configured).
MAX_ATTEMPTS = 5
# Exponential backoff with random jitter, 3s -> 60s between attempts. Jitter
# (up to 100% of the base wait) avoids many workers retrying in lock-step, which
# is exactly what deepens 429/503 overload storms.
BACKOFF_BASE_SECONDS = 3.0
BACKOFF_MAX_SECONDS = 60.0
# Failure kinds that are worth trying the NEXT model for (transient / per-model
# availability). Bad-key, config and empty-response errors are permanent and
# fail fast without wasting calls on other models.
_FALLBACK_KINDS = ("server", "rate limit", "network", "model not found")


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff from 3s up to 60s, plus random jitter.

    ``attempt`` is 1-based (the attempt that just failed). The deterministic
    part is min(3 * 2**(attempt-1), 60); we then add up to +/-50% random jitter
    so concurrent workers de-synchronise instead of stampeding the API.
    """
    base = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
    jitter = base * 0.5 * (2 * random.random() - 1.0)  # in [-0.5, +0.5) * base
    return max(1.0, min(BACKOFF_MAX_SECONDS, base + jitter))


class LLMError(RuntimeError):
    """Raised when an LLM call cannot be completed.

    ``kind`` is one of: ``bad key``, ``model not found``, ``rate limit``,
    ``network``, ``server``, ``unknown``, ``config``. The message is safe to
    print; the API key is never included.
    """

    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _clean_api_key(raw: str) -> str:
    """Strip whitespace and quote characters from a raw key string.

    Keys pasted into ``.env`` often pick up stray spaces or surrounding quotes
    (``GEMINI_API_KEY=" abcd... "``), which the API then rejects as invalid
    credentials. We remove those before use.
    """
    cleaned = raw.strip().strip("\"'").strip()
    # Also remove any embedded whitespace/newlines (copy-paste artefacts).
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def get_gemini_api_key() -> str:
    """Return the cleaned Gemini API key from the environment.

    Raises :class:`LLMError` (kind ``config``) if it is missing or a placeholder.
    """
    raw = os.getenv("GEMINI_API_KEY", "")
    key = _clean_api_key(raw) if raw else ""
    if not key:
        raise LLMError(
            "GEMINI_API_KEY is not set (or is empty). Add it to a .env file: "
            "GEMINI_API_KEY=your_key_here  (get a key at https://aistudio.google.com/apikey). "
            "To run offline without a key, set LLM_MODE=offline in .env.",
            kind="config",
        )
    if key.lower() in {"your_gemini_api_key_here", "your_key_here", "xxx", "changeme"}:
        raise LLMError(
            "GEMINI_API_KEY still contains the placeholder value from .env.example. "
            "Replace it with your real key, or set LLM_MODE=offline to run without one.",
            kind="config",
        )
    return key


def get_model_name() -> str:
    """Return the primary Gemini model name (default ``gemini-2.5-flash``).

    Kept for backward compatibility: it returns the FIRST model of the
    :func:`get_models` fallback chain (from ``GEMINI_MODELS``, else
    ``GEMINI_MODEL``).
    """
    return get_models()[0]


def get_models() -> list[str]:
    """Return the ordered list of Gemini models to try (the fallback chain).

    Reads ``GEMINI_MODELS`` (a comma-separated list, e.g.
    ``gemini-3.8-flash,gemini-2.5-flash``). If unset, falls back to the single
    ``GEMINI_MODEL`` for backward compatibility, then to :data:`DEFAULT_MODEL`.
    Duplicates are removed while preserving order, and blank entries are
    dropped, so ``GEMINI_MODELS=a,,a,b`` yields ``["a", "b"]``.
    """
    raw = os.getenv("GEMINI_MODELS", "").strip()
    models: list[str] = []
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
    if not models:
        single = os.getenv("GEMINI_MODEL", "").strip()
        models = [single] if single else [DEFAULT_MODEL]
    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for m in models:
        if m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def get_llm_mode() -> str:
    """Return the configured LLM mode string: ``online`` (default) or ``offline``."""
    return os.getenv("LLM_MODE", "").strip().lower() or "online"


def is_offline_mode() -> bool:
    """True when the user requested offline mode (``LLM_MODE=offline``)."""
    return get_llm_mode() == "offline"


def proxy_in_use() -> bool:
    """True if an HTTPS_PROXY / HTTP_PROXY environment variable is set."""
    return bool(os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY"))


def _redact(text: str, secret: str | None = None) -> str:
    """Remove any occurrence of the API key from ``text`` before logging."""
    if secret:
        text = text.replace(secret, "<redacted>")
    # Defensive: redact anything that looks like a Gemini key.
    text = re.sub(r"AIza[0-9A-Za-z_\-]{10,}", "<redacted>", text)
    return text


def _describe_proxy() -> str:
    if not proxy_in_use():
        return "proxy: none (direct connection)"
    parts = []
    for var in ("HTTPS_PROXY", "HTTP_PROXY"):
        val = os.getenv(var)
        if val:
            # Do not print credentials embedded in proxy URLs.
            host = re.sub(r"//[^@/]*@", "//<creds>@", val)
            parts.append(f"{var}={host}")
    return "proxy in use: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
# Patterns checked against exception text/status to name the failure kind.
_NETWORK_MARKERS = (
    "econnreset", "connection reset", "connection aborted", "connection refused",
    "read timed out", "timed out", "timeout", "remote end closed",
    "connectionerror", "connecterror", "remoteprotocolerror",
    "temporary failure in name resolution", "getaddrinfo",
    "network is unreachable", "proxyerror", "sslerror",
)


def _status_code_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status code from a google-genai / httpx exception."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        return status
    m = re.search(r"\b([45]\d{2})\b", str(exc))
    return int(m.group(1)) if m else None


def classify_error(exc: BaseException) -> str:
    """Map an exception to a short, readable failure kind.

    Returns one of ``bad key``, ``model not found``, ``rate limit``,
    ``network`` or ``unknown``.
    """
    status = _status_code_of(exc)
    text = str(exc).lower()

    # Key / credentials problems (401/403, invalid API key, permission denied).
    if status in (401, 403) or "api key not valid" in text or "api_key_invalid" in text \
            or "permission denied" in text or "unauthenticated" in text \
            or ("credentials" in text and "reject" in text):
        return "bad key"
    if "api key" in text and ("invalid" in text or "not valid" in text or "reject" in text):
        return "bad key"

    # Model name problems (404, model not found).
    if status == 404 or ("model" in text and "not found" in text) or "is not found" in text:
        return "model not found"

    # Rate limiting (429 / resource exhausted / quota).
    if status == 429 or "rate limit" in text or "resource_exhausted" in text or "quota" in text:
        return "rate limit"

    # Network problems (ECONNRESET, timeouts, DNS, TLS, proxy). These are
    # client-side connectivity issues -- distinct from Google returning a 5xx.
    if any(marker in text for marker in _NETWORK_MARKERS):
        return "network"
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "network"

    # Google-side server errors (503 UNAVAILABLE "high demand", 504 DEADLINE,
    # 500). The request reached Google; the server was overloaded/slow. This is
    # transient capacity, NOT the user's proxy/network, so it is reported
    # distinctly and is retriable.
    if status is not None and status >= 500:
        return "server"

    return "unknown"


def _friendly_message(kind: str, exc: BaseException, secret: str | None = None) -> str:
    """Build a short operator-facing message for a failed call."""
    detail = _redact(f"{type(exc).__name__}: {exc}", secret)[:300]
    hints = {
        "bad key": "The API key was rejected. Check GEMINI_API_KEY in .env (no spaces/quotes needed; llm.py strips them anyway).",
        "model not found": "The model name was rejected (404). Check GEMINI_MODEL in .env -- gemini-2.5-flash is no longer offered to new users; try gemini-3.8-flash.",
        "rate limit": "Gemini rate limit hit (429). Wait a minute, lower SCD_LLM_MAX_RPM/SCD_CHUNK_BATCH_SIZE, or rerun (cached calls are skipped).",
        "network": "Network failure reaching generativelanguage.googleapis.com (DNS/TLS/ECONNRESET/timeout). Check internet or set HTTPS_PROXY in .env; or run with LLM_MODE=offline.",
        "server": "Google's server was overloaded/unavailable (503/504 'high demand'). This is transient and Google-side -- not your key or network. Rerun in a bit; cached calls are skipped and the retry/backoff already tried 5 times.",
        "unknown": "Unclassified Gemini failure.",
    }
    return f"[{kind}] {hints.get(kind, '')} ({detail})"


# ---------------------------------------------------------------------------
# Caching + shared behaviour
# ---------------------------------------------------------------------------
def _cache_parts(prompt: str, system: str | None, kind: str) -> tuple:
    """Cache-key components. The hash covers ``(kind, system, prompt)`` -- NOT the
    model -- so a result produced by ANY model is reused across reruns and an
    interrupted run resumes without repeating a call (per spec: key by
    (prompt, task), where ``kind`` is the task: ``text`` or ``json``).
    """
    return (kind, system or "", prompt)


class BaseLLM:
    """Common disk-caching wrapper around a text-completion backend.

    Subclasses only implement ``_complete_text`` / ``_complete_json`` (raw
    transport). All cache lookup/storage happens here, keyed by SHA-256 of
    (task/kind, system, prompt) via :mod:`scd.cache` -- deliberately NOT the
    model -- so a response produced by any model is reused across reruns and an
    interrupted run resumes without repeating a call.
    """

    provider_name = "base"

    def __init__(self, model: str | None = None, use_cache: bool = True) -> None:
        self.model = model or get_model_name()
        self.use_cache = use_cache
        # Lightweight, thread-safe counters so callers can log resume progress
        # ("30 cache hits, 5 new calls"). Safe to use from a thread pool.
        self._stats_lock = threading.Lock()
        self._cache_hits = 0
        self._new_calls = 0

    def stats(self) -> dict:
        """Return a snapshot of cache-hit / new-call counts."""
        with self._stats_lock:
            return {"cache_hits": self._cache_hits, "new_calls": self._new_calls}

    def is_cached(self, prompt: str, *, system: str | None = None, kind: str = "json") -> bool:
        """True if a response for this exact (system, prompt, task) is on disk.

        Lets callers (e.g. the ledger) count how much of a run is already cached
        so an interrupted job can log "resuming: X of Y already done" without
        re-issuing any network call.
        """
        if not self.use_cache:
            return False
        key_parts = _cache_parts(prompt, system, kind)
        cached = cache.get_json(*key_parts, namespace="llm")
        if kind == "json":
            return isinstance(cached, dict) and "data" in cached
        return isinstance(cached, dict) and "text" in cached

    def _record(self, *, hit: bool) -> None:
        with self._stats_lock:
            if hit:
                self._cache_hits += 1
            else:
                self._new_calls += 1

    # -- public API --------------------------------------------------------
    def complete_text(self, prompt: str, *, system: str | None = None) -> str:
        """Return a plain-text completion for ``prompt`` (cached on disk)."""
        key_parts = _cache_parts(prompt, system, "text")
        if self.use_cache:
            cached = cache.get_json(*key_parts, namespace="llm")
            if isinstance(cached, dict) and "text" in cached:
                logger.debug("LLM cache hit (text)")
                self._record(hit=True)
                return cached["text"]
        text = self._complete_text(prompt, system=system)
        if self.use_cache:
            cache.set_json({"text": text}, *key_parts, namespace="llm")
        self._record(hit=False)
        return text

    def complete_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        expected: type | tuple = (dict, list),
    ) -> Any:
        """Return a parsed-JSON completion for ``prompt`` (cached on disk)."""
        key_parts = _cache_parts(prompt, system, "json")
        if self.use_cache:
            cached = cache.get_json(*key_parts, namespace="llm")
            if isinstance(cached, dict) and "data" in cached:
                logger.debug("LLM cache hit (json)")
                self._record(hit=True)
                return cached["data"]
        raw = self._complete_json(prompt, system=system)
        data = _coerce_json(raw, expected)
        if self.use_cache:
            cache.set_json({"data": data}, *key_parts, namespace="llm")
        self._record(hit=False)
        return data

    # -- to be implemented by subclasses ----------------------------------
    def _complete_text(self, prompt: str, *, system: str | None = None) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def _complete_json(self, prompt: str, *, system: str | None = None) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Gemini backend (real calls via google-genai)
# ---------------------------------------------------------------------------
class GeminiLLM(BaseLLM):
    """Gemini via the official google-genai SDK, with multi-model fallback.

    * Per-request timeout defaults to 120s (``SCD_LLM_TIMEOUT_SECONDS``;
      ``HttpOptions.timeout`` is in milliseconds).
    * Tries each model in the ``GEMINI_MODELS`` chain in order (falling back to
      ``GEMINI_MODEL`` for a single model). Each model gets ``MODEL_ATTEMPTS``
      (3) tries; if it keeps failing with 503/504, 429, or an SSL/connection
      error, we automatically move to the next model.
    * Retries transient failures with exponential backoff + random jitter
      (3s -> 60s between attempts).
    * Non-retriable failures (bad key, config) fail immediately without trying
      other models. ``model not found`` DOES fall back (a retired model name is
      a common, transient-in-practice configuration issue).
    * Prints which model actually answered each call, so fallback is visible.
    * Honours HTTPS_PROXY / HTTP_PROXY (httpx inside the SDK reads them).
    * The API key is never logged or printed.
    """

    provider_name = "gemini"

    def __init__(self, model: str | None = None, use_cache: bool = True, api_key: str | None = None) -> None:
        super().__init__(model=model, use_cache=use_cache)
        self._api_key = api_key or get_gemini_api_key()  # raises LLMError(kind="config") if missing
        self._client = None
        # Fallback chain: an explicit `model` arg pins a single model; otherwise
        # use the GEMINI_MODELS list (or GEMINI_MODEL for backward compat).
        self.models: list[str] = [model] if model else get_models()
        self.model = self.models[0]
        # The model that actually answered the most recent call (for visibility).
        self.last_model_used: str | None = None
        logger.info("Gemini client configured: models=%s | %s",
                    ", ".join(self.models), _describe_proxy())
        # NOTE: httpx (used by google-genai) picks up HTTPS_PROXY/HTTP_PROXY
        # automatically; we only report it. The key stays in memory only.

    @property
    def client(self):
        """Lazily construct the SDK client with the configured timeout (120s)."""
        if self._client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise LLMError(
                    "The 'google-genai' package is not installed. Run: pip install google-genai",
                    kind="config",
                ) from exc
            # HttpOptions.timeout is in MILLISECONDS (verified against SDK).
            http_options = types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)
            self._client = genai.Client(api_key=self._api_key, http_options=http_options)
        return self._client

    # -- retrying single call ---------------------------------------------
    def _call_once(self, prompt: str, *, system: str | None, want_json: bool, model: str) -> str:
        from google.genai import types

        gen_config = types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json" if want_json else "text/plain",
        )
        if system:
            gen_config.system_instruction = system

        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=gen_config,
        )
        text = getattr(response, "text", None)
        if not text:
            raise LLMError("Gemini returned an empty response.", kind="unknown")
        return text

    def _call_with_retry(self, prompt: str, *, system: str | None, want_json: bool) -> str:
        """Call Gemini with multi-model fallback, retry and jittered backoff.

        Strategy (per spec):
        * Walk the model chain in order. For each model, allow up to
          ``MODEL_ATTEMPTS`` (3) tries.
        * Retry a given model on transient failures (network, rate limit, and
          Google-side 5xx ``server``) using exponential backoff with random
          jitter (3s -> 60s).
        * If a model exhausts its attempts and the last failure is one of the
          fallback-worthy kinds, move on to the NEXT model in the chain.
        * Permanent failures (bad key, config, empty response) abort immediately
          without burning calls on other models.
        * Records and prints which model actually answered, so fallback is
          visible in the logs.
        """
        secret = self._api_key
        last_exc: BaseException | None = None

        for model_index, model in enumerate(self.models):
            for attempt in range(1, MODEL_ATTEMPTS + 1):
                # Track the model in use so _call_once / error messages are scoped.
                self.model = model
                try:
                    text = self._call_once(prompt, system=system, want_json=want_json, model=model)
                    self.last_model_used = model
                    # A clear, always-on line so the operator can SEE the fallback
                    # working (which model actually answered this call).
                    print(f"[llm] answered by model: {model}", flush=True)
                    logger.info("Gemini call answered by model: %s", model)
                    return text
                except LLMError as exc:
                    # Already classified (e.g. empty response) -- permanent, abort.
                    raise
                except Exception as exc:
                    last_exc = exc
                    kind = classify_error(exc)
                    logger.warning(
                        "Gemini call failed on model %s (attempt %d/%d): %s | %s",
                        model, attempt, MODEL_ATTEMPTS, kind, _redact(str(exc), secret)[:200],
                    )
                    retriable = kind in _FALLBACK_KINDS
                    is_last_attempt_for_model = attempt == MODEL_ATTEMPTS
                    has_next_model = model_index < len(self.models) - 1

                    # Permanent failure -> fail fast (do not try other models).
                    if not retriable:
                        raise LLMError(_friendly_message(kind, exc, secret), kind=kind) from exc

                    # Give up retrying this model once it has used its attempts.
                    if is_last_attempt_for_model:
                        break

                    # Exponential backoff + random jitter (3s -> 60s) before retry.
                    wait = _backoff_seconds(attempt)
                    logger.info(
                        "Retrying model %s in %.1fs (attempt %d/%d)...",
                        model, wait, attempt, MODEL_ATTEMPTS,
                    )
                    time.sleep(wait)

            # This model exhausted its attempts. If its failure is fallback-worthy
            # and another model remains, continue to it; otherwise stop.
            if has_next_model and last_exc is not None and classify_error(last_exc) in _FALLBACK_KINDS:
                logger.warning(
                    "Model %s failed after %d attempts (%s); falling back to %s",
                    model, MODEL_ATTEMPTS, classify_error(last_exc), self.models[model_index + 1],
                )
                continue
            break

        # All models failed. Report the last failure with a helpful message.
        kind = classify_error(last_exc) if last_exc is not None else "network"
        raise LLMError(
            _friendly_message(kind, last_exc or RuntimeError("no attempt"), secret), kind=kind,
        )


    def _complete_text(self, prompt: str, *, system: str | None = None) -> str:
        return self._call_with_retry(prompt, system=system, want_json=False)

    def _complete_json(self, prompt: str, *, system: str | None = None) -> str:
        return self._call_with_retry(prompt, system=system, want_json=True)


# ---------------------------------------------------------------------------
# Mock backend (offline testing without an API key)
# ---------------------------------------------------------------------------
class MockLLM(BaseLLM):
    """Deterministic offline backend for testing the pipeline without a key.

    By default it returns small, valid, generic payloads so downstream code
    paths can be exercised end to end. For richer offline demos/tests install a
    *script* callable via :meth:`set_script`; the script receives
    ``(prompt, system)`` and returns the raw string the backend would produce
    (see :mod:`scd.demo`). Real findings require the Gemini provider.
    """

    provider_name = "mock"

    def __init__(self, model: str | None = None, use_cache: bool = True) -> None:
        super().__init__(model=model, use_cache=use_cache)
        self._script = None  # Optional[Callable[[str, str|None], str]]

    def set_script(self, script) -> None:
        """Install a scripted responder: ``script(prompt, system) -> str``.

        When a script is installed, caching is bypassed so each call is routed
        straight to the script (keeps demos independent of the cache dir).
        """
        self._script = script
        if script is not None:
            self.use_cache = False

    def _complete_text(self, prompt: str, *, system: str | None = None) -> str:
        if self._script is not None:
            return self._script(prompt, system)
        return "[mock] " + prompt[:120].replace("\n", " ")

    def _complete_json(self, prompt: str, *, system: str | None = None) -> str:
        if self._script is not None:
            return self._script(prompt, system)
        return json.dumps({"mock": True, "note": "offline mock response", "items": []})


# ---------------------------------------------------------------------------
# JSON coercion helpers
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _coerce_json(raw: str, expected: type | tuple) -> Any:
    """Best-effort parse of a model reply that should contain JSON."""
    if isinstance(raw, (dict, list)):
        return raw
    text = (raw or "").strip()

    candidates = [text]
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.insert(0, fence.group(1).strip())

    # As a last resort, slice from the first { or [ to the last } or ].
    for src in list(candidates):
        for opener, closer in (("{", "}"), ("[", "]")):
            start = src.find(opener)
            end = src.rfind(closer)
            if start != -1 and end != -1 and end > start:
                candidates.append(src[start : end + 1])

    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, expected):
            return data
    raise LLMError("Model response was not valid JSON in the expected shape.", kind="unknown")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def get_llm(model: str | None = None, use_cache: bool = True) -> BaseLLM:
    """Return the configured LLM backend.

    Selection order:
    1. ``LLM_MODE=offline`` in ``.env`` -> the demo scripted LLM (no network,
       no API key; pipeline remains fully testable).
    2. ``SCD_LLM_PROVIDER=mock`` -> plain :class:`MockLLM`.
    3. Otherwise -> :class:`GeminiLLM` (raises a clear error if the key is
       missing or the SDK is not installed).
    """
    if is_offline_mode():
        logger.info("LLM_MODE=offline: using the demo scripted LLM (no network calls).")
        from .demo import scripted_llm  # local import: demo imports MockLLM from here
        llm = scripted_llm()
        if model:
            llm.model = model
        return llm
    if os.getenv("SCD_LLM_PROVIDER", "gemini").strip().lower() == "mock":
        return MockLLM(model=model, use_cache=use_cache)
    return GeminiLLM(model=model, use_cache=use_cache)




