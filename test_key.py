"""One-shot connectivity + credentials diagnostic for the Gemini LLM layer.

Run this FIRST when LLM calls fail. It answers three questions in order:

1. Is the network itself blocked? (plain HTTPS GET to
   https://generativelanguage.googleapis.com -- no API key involved)
2. Is GEMINI_API_KEY configured? (checks .env / environment; never prints it)
3. Does a tiny real Gemini call succeed? (through scd/llm.py, with its retry
   and error classification; bypasses the disk cache and offline mode)

Usage:
    python test_key.py

Reading the output:
    * NETWORK BLOCKED -> the "read ECONNRESET" you see is a firewall/proxy
      issue: set HTTPS_PROXY in .env if you use one, or run with LLM_MODE=offline.
    * KEY problems -> your key was rejected; fix GEMINI_API_KEY in .env.
      (Quoted or space-padded keys are fine: llm.py strips them.)
    * LLM [rate limit] -> wait a minute and retry; cached calls are never repeated.
    * LLM OK while the pipeline still fails -> the problem is in a pipeline
      module, not in connectivity; check its logs.

Exit codes: 0 = all OK, 1 = Gemini call failed, 2 = network/key not usable.
"""

from __future__ import annotations

import os
import sys
import time

# Make the project importable when run as `python test_key.py` from anywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()  # pick up .env before llm.py reads the environment

from scd import llm as llm_mod  # noqa: E402  (after load_dotenv)

GENERIC_URL = "https://generativelanguage.googleapis.com/"
TIMEOUT_SECONDS = 20


def check_network() -> bool:
    """Plain HTTPS GET to the Gemini API host. No key, no SDK."""
    print(f"[1/3] HTTPS reachability: GET {GENERIC_URL} (timeout {TIMEOUT_SECONDS}s)")
    try:
        import requests
    except ImportError:
        print("      (requests not installed; falling back to urllib)")
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(GENERIC_URL, timeout=TIMEOUT_SECONDS) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code  # 4xx still proves the host is reachable
        except Exception as exc:
            print(f"NETWORK : BLOCKED ({type(exc).__name__}: {exc})")
            return False
        print(f"NETWORK : OK (HTTP {status} from generativelanguage.googleapis.com)")
        return True

    try:
        resp = requests.get(GENERIC_URL, timeout=TIMEOUT_SECONDS)
        print(f"NETWORK : OK (HTTP {resp.status_code} from generativelanguage.googleapis.com)")
        return True
    except Exception as exc:
        print(f"NETWORK : BLOCKED ({type(exc).__name__}: {str(exc)[:200]})")
        print("         -> If you use a corporate proxy, set HTTPS_PROXY (and HTTP_PROXY) in .env.")
        print("         -> Or set LLM_MODE=offline in .env to test the pipeline without internet.")
        return False


def check_key() -> bool:
    """Report whether a usable-looking key is configured (never prints it)."""
    print("[2/3] GEMINI_API_KEY configuration")
    print(f"      proxy in use: {'yes' if llm_mod.proxy_in_use() else 'no'}"
          f" (HTTPS_PROXY={'set' if os.getenv('HTTPS_PROXY') else 'unset'}"
          f", HTTP_PROXY={'set' if os.getenv('HTTP_PROXY') else 'unset'})")
    print(f"      model: {llm_mod.get_model_name()}")
    print(f"      LLM_MODE: {llm_mod.get_llm_mode()}")
    raw = os.getenv("GEMINI_API_KEY", "")
    if not raw:
        print("KEY     : MISSING -- no GEMINI_API_KEY in .env or environment.")
        print("         -> Get a key at https://aistudio.google.com/apikey and add it to .env.")
        return False
    key = llm_mod._clean_api_key(raw)
    if len(key) != len(raw):
        print(f"      note: key was cleaned ({len(raw)} -> {len(key)} chars: spaces/quotes stripped)")
    if key.lower() in {"your_gemini_api_key_here", "your_key_here", "xxx", "changeme"}:
        print("KEY     : PLACEHOLDER -- .env still contains the example value, not a real key.")
        return False
    print(f"KEY     : OK (present, {len(key)} chars; value never printed)")
    return True


def check_llm_call() -> bool:
    """Make one tiny real Gemini call through scd/llm.py (fresh, uncached)."""
    print("[3/3] Tiny Gemini call through scd/llm.py")
    try:
        # Bypass cache + offline mode: we want a REAL call for diagnosis.
        client = llm_mod.GeminiLLM(use_cache=False)
    except llm_mod.LLMError as exc:
        print(f"LLM     : FAILED [{exc.kind}] {exc}")
        return False

    started = time.time()
    try:
        reply = client.complete_text("Reply with exactly one word: OK")
    except llm_mod.LLMError as exc:
        elapsed = time.time() - started
        print(f"LLM     : FAILED [{exc.kind}] after {elapsed:.1f}s")
        print(f"         {exc}")
        if exc.kind == "network":
            print("         -> Connectivity problem; see NETWORK check above.")
        elif exc.kind == "bad key":
            print("         -> The key exists but was rejected: it may be revoked,")
            print("            expired, or belong to a different Google Cloud project.")
        return False
    except Exception as exc:  # unclassified -- still report, never crash
        print(f"LLM     : FAILED [unhandled {type(exc).__name__}] {str(exc)[:200]}")
        return False

    elapsed = time.time() - started
    snippet = reply.strip().replace("\n", " ")[:60]
    print(f"LLM     : OK ({elapsed:.1f}s, model replied: {snippet!r})")
    return True


def main() -> int:
    print("=" * 72)
    print("Silent Change Detector -- Gemini connectivity diagnostic")
    print("=" * 72)
    network_ok = check_network()
    key_ok = check_key()

    if not network_ok and not key_ok:
        print("\nVERDICT : 2 problems -- network blocked AND key missing.")
        print("          Fix the network (or use LLM_MODE=offline), then re-run.")
        return 2

    if not network_ok:
        print("\nVERDICT : network blocked (key status above).")
        print("          LLM calls will keep failing with [network] until fixed.")
        return 2

    if not key_ok:
        print("\nVERDICT : network OK, but the key is not configured.")
        print("          Add GEMINI_API_KEY to .env, then re-run this script.")
        return 2

    if llm_mod.is_offline_mode():
        print("\nnote: LLM_MODE=offline is set; forcing a REAL call anyway for diagnosis.")

    if check_llm_call():
        print("\nVERDICT : OK -- network, key and model all work. Pipeline can run online.")
        return 0

    print("\nVERDICT : network + key look fine, but the Gemini call failed (kind above).")
    return 1


if __name__ == "__main__":
    sys.exit(main())

