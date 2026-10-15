"""Unit tests for the robust LLM layer (:mod:`scd.llm`).

These guard the "diagnose, don't lie" contract: error classification must name
the real failure kind, and the API key must never appear in any output.
"""

from __future__ import annotations

import os

import pytest

from scd import llm


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
class _FakeExc(Exception):
    def __init__(self, text: str, code: int | None = None) -> None:
        super().__init__(text)
        if code is not None:
            self.code = code


class TestClassifyError:
    def test_econnreset_is_network(self):
        assert llm.classify_error(_FakeExc("read ECONNRESET")) == "network"

    def test_connection_reset_text_is_network(self):
        assert llm.classify_error(_FakeExc("Connection reset by peer")) == "network"

    def test_timeout_is_network(self):
        assert llm.classify_error(_FakeExc("Read timed out.")) == "network"

    def test_401_is_bad_key(self):
        assert llm.classify_error(_FakeExc("boom", code=401)) == "bad key"

    def test_403_is_bad_key(self):
        assert llm.classify_error(_FakeExc("permission denied", code=403)) == "bad key"

    def test_api_key_invalid_is_bad_key(self):
        assert llm.classify_error(_FakeExc("API key not valid. Please pass a valid key.")) == "bad key"

    def test_rejected_credentials_is_bad_key(self):
        assert llm.classify_error(_FakeExc("request rejected: credentials could not be verified")) == "bad key"

    def test_404_is_model_not_found(self):
        assert llm.classify_error(_FakeExc("no such model", code=404)) == "model not found"

    def test_model_not_found_text(self):
        assert llm.classify_error(_FakeExc("models/gemini-2.5-flash is not found")) == "model not found"

    def test_429_is_rate_limit(self):
        assert llm.classify_error(_FakeExc("quota exceeded", code=429)) == "rate limit"

    def test_resource_exhausted_is_rate_limit(self):
        assert llm.classify_error(_FakeExc("RESOURCE_EXHAUSTED")) == "rate limit"

    def test_503_is_server(self):
        assert llm.classify_error(_FakeExc("UNAVAILABLE", code=503)) == "server"

    def test_504_is_server(self):
        assert llm.classify_error(_FakeExc("DEADLINE_EXCEEDED", code=504)) == "server"

    def test_high_demand_503_text_is_server(self):
        # No explicit code attr, but the 503 is embedded in the message.
        assert llm.classify_error(_FakeExc("503 UNAVAILABLE high demand")) == "server"

    def test_unknown_when_nothing_matches(self):
        assert llm.classify_error(_FakeExc("some weird internal thing")) == "unknown"


# ---------------------------------------------------------------------------
# Redaction (never leak the key)
# ---------------------------------------------------------------------------
class TestRedaction:
    def test_removes_exact_secret(self):
        assert llm._redact("failed with key ABC123 here", "ABC123") == \
            "failed with key <redacted> here"

    def test_removes_gemini_style_keys_even_without_secret(self):
        fake = "AIzaSyA1234567890abcdefghij"
        out = llm._redact(f"error using {fake}")
        assert fake not in out
        assert "<redacted>" in out

    def test_friendly_message_never_contains_secret(self):
        secret = "AIzaTESTKEY1234567890"
        exc = _FakeExc(f"call failed using {secret}", code=503)
        msg = llm._friendly_message("server", exc, secret)
        assert secret not in msg


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_clean_api_key_strips_spaces_and_quotes(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", '  "abc123"  ')
        assert llm._clean_api_key(os.getenv("GEMINI_API_KEY")) == "abc123"

    def test_model_default(self, monkeypatch):
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        assert llm.get_model_name() == llm.DEFAULT_MODEL

    def test_model_override(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
        assert llm.get_model_name() == "gemini-3.8-flash"

    def test_offline_mode_detection(self, monkeypatch):
        monkeypatch.setenv("LLM_MODE", "offline")
        assert llm.is_offline_mode() is True
        monkeypatch.setenv("LLM_MODE", "online")
        assert llm.is_offline_mode() is False


# ---------------------------------------------------------------------------
# Factory: offline mode returns the scripted demo backend
# ---------------------------------------------------------------------------
class TestFactory:
    def test_offline_mode_returns_mock(self, monkeypatch):
        monkeypatch.setenv("LLM_MODE", "offline")
        monkeypatch.setenv("SCD_LLM_PROVIDER", "gemini")
        backend = llm.get_llm()
        assert isinstance(backend, llm.MockLLM)


# ---------------------------------------------------------------------------
# Multi-model fallback chain (GEMINI_MODELS)
# ---------------------------------------------------------------------------
class TestGetModels:
    def test_from_models_list(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODELS", "gemini-3.8-flash, gemini-2.5-flash")
        assert llm.get_models() == ["gemini-3.8-flash", "gemini-2.5-flash"]

    def test_drops_blanks_and_dupes(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODELS", "a,,a,b")
        assert llm.get_models() == ["a", "b"]

    def test_backward_compat_single_model(self, monkeypatch):
        monkeypatch.delenv("GEMINI_MODELS", raising=False)
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
        assert llm.get_models() == ["gemini-3.8-flash"]

    def test_default_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_MODELS", raising=False)
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        assert llm.get_models() == [llm.DEFAULT_MODEL]


class TestBackoff:
    def test_starts_near_three_seconds(self):
        # attempt 1 -> base 3s, +/-50% jitter -> within [1.5, 4.5]
        for _ in range(50):
            assert 1.5 <= llm._backoff_seconds(1) <= 4.5

    def test_capped_at_sixty(self):
        # Large attempts must never exceed the 60s ceiling.
        for attempt in (5, 8, 20, 100):
            assert llm._backoff_seconds(attempt) <= llm.BACKOFF_MAX_SECONDS

    def test_grows_then_saturates(self):
        # attempt 2's midpoint (6s) is above attempt 1's (3s); the deterministic
        # base saturates at BACKOFF_MAX by attempt ~6.
        base1 = min(llm.BACKOFF_BASE_SECONDS * (2 ** 0), llm.BACKOFF_MAX_SECONDS)
        base2 = min(llm.BACKOFF_BASE_SECONDS * (2 ** 1), llm.BACKOFF_MAX_SECONDS)
        assert base2 > base1
        assert min(llm.BACKOFF_BASE_SECONDS * (2 ** 6), llm.BACKOFF_MAX_SECONDS) == llm.BACKOFF_MAX_SECONDS


class TestModelFallback:
    """Exercise GeminiLLM._call_with_retry's fallback without any network."""

    def _client(self) -> "llm.GeminiLLM":
        gem = llm.GeminiLLM(api_key="__test_key__", use_cache=False)
        gem.models = ["model-a", "model-b"]
        return gem

    def test_falls_back_to_next_model_on_503(self, monkeypatch, capsys):
        gem = self._client()
        calls = {"model-a": 0, "model-b": 0}

        def fake_call_once(self, prompt, *, system, want_json, model):
            if model == "model-a":
                calls["model-a"] += 1
                raise _FakeExc("503 UNAVAILABLE high demand", code=503)
            calls["model-b"] += 1
            return "OK from B"

        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        monkeypatch.setattr(llm.GeminiLLM, "_call_once", fake_call_once)

        result = gem._call_with_retry("hi", system=None, want_json=False)

        assert result == "OK from B"
        # First model gets MODEL_ATTEMPTS tries, then we fall back.
        assert calls["model-a"] == llm.MODEL_ATTEMPTS
        assert calls["model-b"] == 1
        assert gem.last_model_used == "model-b"
        # The answering model is printed so the fallback is visible.
        assert "answered by model: model-b" in capsys.readouterr().out

    def test_falls_back_on_connection_error(self, monkeypatch):
        gem = self._client()
        calls = {"n": 0}

        def fake_call_once(self, prompt, *, system, want_json, model):
            calls["n"] += 1
            if model == "model-a":
                raise _FakeExc("read ECONNRESET")
            return "OK via B"

        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        monkeypatch.setattr(llm.GeminiLLM, "_call_once", fake_call_once)

        assert gem._call_with_retry("hi", system=None, want_json=False) == "OK via B"
        assert gem.last_model_used == "model-b"

    def test_bad_key_does_not_fall_back(self, monkeypatch):
        gem = self._client()
        calls = {"n": 0}

        def fake_call_once(self, prompt, *, system, want_json, model):
            calls["n"] += 1
            raise _FakeExc("API key not valid", code=400)

        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        monkeypatch.setattr(llm.GeminiLLM, "_call_once", fake_call_once)

        with pytest.raises(llm.LLMError):
            gem._call_with_retry("hi", system=None, want_json=False)
        # A permanent (bad-key) failure aborts on the FIRST model only.
        assert calls["n"] == 1
        assert gem.last_model_used is None

    def test_first_model_success_does_not_try_others(self, monkeypatch):
        gem = self._client()
        calls = {"n": 0}

        def fake_call_once(self, prompt, *, system, want_json, model):
            calls["n"] += 1
            return "OK from A"

        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        monkeypatch.setattr(llm.GeminiLLM, "_call_once", fake_call_once)

        assert gem._call_with_retry("hi", system=None, want_json=False) == "OK from A"
        assert calls["n"] == 1
        assert gem.last_model_used == "model-a"

    def test_all_models_fail_raises(self, monkeypatch):
        gem = self._client()

        def fake_call_once(self, prompt, *, system, want_json, model):
            raise _FakeExc("503 UNAVAILABLE", code=503)

        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        monkeypatch.setattr(llm.GeminiLLM, "_call_once", fake_call_once)

        with pytest.raises(llm.LLMError) as excinfo:
            gem._call_with_retry("hi", system=None, want_json=False)
        assert excinfo.value.kind == "server"
