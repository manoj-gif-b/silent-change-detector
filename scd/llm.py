"""Robust LLM access layer (Gemini via google-genai) with an offline mode.

This is the ONLY module in the project that talks to the LLM. Every other
module (ledger, detectors, hypothesis, redteam, run.py, app.py) imports
``get_llm()`` from here and calls ``complete_text`` / ``complete_json``.

Features (per project spec):

* Loads ``GEMINI_API_KEY`` and ``GEMINI_MODEL`` (default ``gemini-2.5-flash``)
  from ``.env``; the key is stripped of spaces and quotes. A missing key raises
  a clear, actionable error.
* Calls Gemini through the official ``google-genai`` package.
* Retries transient failures (ECONNRESET, timeouts, HTTP 429 and 5xx) with
  exponential backoff: up to 5 attempts, 2s -> 30s between attempts.
* 60-second per-request timeout (``HttpOptions.timeout`` is in milliseconds).
* Every response is cached on disk by SHA-256 of (model, system, prompt) via
  :mod:`scd.cache`, so reruns never repeat a call.
* Failures are classified into short, readable kinds: ``bad key``,
  ``model not found``, ``rate limit``, ``network`` or ``unknown``. The API key
  is NEVER logged or printed (see :func:`_redact`).
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
import re
import time
from typing import Any

from dotenv import load_dotenv

from . import cache

logger = logging.getLogger("scd.llm")

# Load .env early (idempotent; existing env vars win, matching config.py).
load_dotenv()

DEFAULT_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT_MS = 60_000          # 60 seconds per request
MAX_ATTEMPTS = 5                     # total attempts for retriable failures
BACKOFF_BASE_SECONDS = 2.0           # first wait after attempt 1
BACKOFF_MAX_SECONDS = 30.0           # cap for any single wait


class LLMError(RuntimeError):
    """Raised when an LLM call cannot be completed.

    ``kind`` is one of: ``bad key``, ``model not found``, ``rate limit``,
    ``network``, ``unknown``, ``config``. The message is safe to print; the API
    key is never included.
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
    """Return the Gemini model name (default ``gemini-2.5-flash``)."""
    return os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


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

    # Network problems (ECONNRESET, timeouts, DNS, TLS, proxy, 5xx).
    if any(marker in text for marker in _NETWORK_MARKERS):
        return "network"
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "network"
    if status is not None and status >= 500:
        return "network"

    return "unknown"


def _friendly_message(kind: str, exc: BaseException, secret: str | None = None) -> str:
    """Build a short operator-facing message for a failed call."""
    detail = _redact(f"{type(exc).__name__}: {exc}", secret)[:300]
    hints = {
        "bad key": "The API key was rejected. Check GEMINI_API_KEY in .env (no spaces/quotes needed; llm.py strips them anyway).",
        "model not found": "The model name was rejected. Check GEMINI_MODEL in .env (default: gemini-2.5-flash).",
        "rate limit": "Gemini rate limit hit. Wait a minute, reduce SCD_CHUNK_BATCH_SIZE, or rerun (cached calls are skipped).",
        "network": "Network failure reaching generativelanguage.googleapis.com. Check internet/proxy (HTTPS_PROXY env var) or run with LLM_MODE=offline.",
        "unknown": "Unclassified Gemini failure.",
    }
    return f"[{kind}] {hints.get(kind, '')} ({detail})"


# ---------------------------------------------------------------------------
# Caching + shared behaviour
# ---------------------------------------------------------------------------
def _cache_parts(model: str, prompt: str, system: str | None, kind: str) -> tuple:
    """Stable cache-key components: hash covers (model, system, prompt)."""
    return (kind, model, system or "", prompt)


class BaseLLM:
    """Common disk-caching wrapper around a text-completion backend.

    Subclasses only implement ``_complete_text`` / ``_complete_json`` (raw
    transport). All cache lookup/storage happens here, keyed by SHA-256 of
    (model, system, prompt) via :mod:`scd.cache`, so re-running the pipeline
    never repeats a call.
    """

    provider_name = "base"

    def __init__(self, model: str | None = None, use_cache: bool = True) -> None:
        self.model = model or get_model_name()
        self.use_cache = use_cache

    # -- public API --------------------------------------------------------
    def complete_text(self, prompt: str, *, system: str | None = None) -> str:
        """Return a plain-text completion for ``prompt`` (cached on disk)."""
        key_parts = _cache_parts(self.model, prompt, system, "text")
        if self.use_cache:
            cached = cache.get_json(*key_parts, namespace="llm")
            if isinstance(cached, dict) and "text" in cached:
                logger.debug("LLM cache hit (text)")
                return cached["text"]
        text = self._complete_text(prompt, system=system)
        if self.use_cache:
            cache.set_json({"text": text}, *key_parts, namespace="llm")
        return text

    def complete_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        expected: type | tuple = (dict, list),
    ) -> Any:
        """Return a parsed-JSON completion for ``prompt`` (cached on disk)."""
        key_parts = _cache_parts(self.model, prompt, system, "json")
        if self.use_cache:
            cached = cache.get_json(*key_parts, namespace="llm")
            if isinstance(cached, dict) and "data" in cached:
                logger.debug("LLM cache hit (json)")
                return cached["data"]
        raw = self._complete_json(prompt, system=system)
        data = _coerce_json(raw, expected)
        if self.use_cache:
            cache.set_json({"data": data}, *key_parts, namespace="llm")
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
    """Gemini via the official google-genai SDK.

    * 60s per-request timeout (HttpOptions.timeout is milliseconds).
    * Retries ECONNRESET / timeouts / 429 / 5xx with exponential backoff
      (attempts 1..5, waits 2s, 4s, 8s, 16s; each capped at 30s).
    * Non-retriable failures (bad key, model not found) fail immediately with
      a classified, operator-friendly error.
    * Honours HTTPS_PROXY / HTTP_PROXY (httpx inside the SDK reads them).
    * The API key is never logged or printed.
    """

    provider_name = "gemini"

    def __init__(self, model: str | None = None, use_cache: bool = True, api_key: str | None = None) -> None:
        super().__init__(model=model, use_cache=use_cache)
        self._api_key = api_key or get_gemini_api_key()  # raises LLMError(kind="config") if missing
        self._client = None
        logger.info("Gemini client configured: model=%s | %s", self.model, _describe_proxy())
        # NOTE: httpx (used by google-genai) picks up HTTPS_PROXY/HTTP_PROXY
        # automatically; we only report it. The key stays in memory only.

    @property
    def client(self):
        """Lazily construct the SDK client with a 60-second timeout."""
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
    def _call_once(self, prompt: str, *, system: str | None, want_json: bool) -> str:
        from google.genai import types

        gen_config = types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json" if want_json else "text/plain",
        )
        if system:
            gen_config.system_instruction = system

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=gen_config,
        )
        text = getattr(response, "text", None)
        if not text:
            raise LLMError("Gemini returned an empty response.", kind="unknown")
        return text

    def _call_with_retry(self, prompt: str, *, system: str | None, want_json: bool) -> str:
        """Call Gemini, retrying retriable failures with exponential backoff."""
        secret = self._api_key
        last_exc: BaseException | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._call_once(prompt, system=system, want_json=want_json)
            except LLMError as exc:
                # Already classified (e.g. empty response) -- do not retry.
                raise
            except Exception as exc:
                last_exc = exc
                kind = classify_error(exc)
                retriable = kind in ("network", "rate limit")
                logger.warning(
                    "Gemini call attempt %d/%d failed (%s): %s",
                    attempt, MAX_ATTEMPTS, kind, _redact(str(exc), secret)[:200],
                )
                if not retriable or attempt == MAX_ATTEMPTS:
                    raise LLMError(_friendly_message(kind, exc, secret), kind=kind) from exc
                # Exponential backoff: 2s, 4s, 8s, 16s -- each capped at 30s.
                wait = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
                logger.info("Retrying in %.0fs (backoff %d/%d)...",
                            wait, attempt, MAX_ATTEMPTS - 1)
                time.sleep(wait)
        # Defensive: loop always returns or raises.
        raise LLMError(_friendly_message("network", last_exc or RuntimeError("no attempt"), secret),
                       kind="network")

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




