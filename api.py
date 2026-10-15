"""FastAPI backend for the Silent Change Detector web UI.

Exposes the existing Python pipeline to a Next.js frontend over HTTP. It never
touches, reads, prints or logs ``.env`` or ``GEMINI_API_KEY`` -- the key lives
only in the server process (loaded by ``scd.config`` on import) and is used
solely inside the pipeline. Nothing here exposes credentials to the client.

Endpoints
---------
* ``GET  /health``                    -> liveness + whether any key is present (bool only)
* ``GET  /companies``                 -> companies that have a finished report.json
* ``GET  /report/{slug}``             -> the raw report.json for that company
* ``GET  /report/{slug}/pdf``         -> that report rendered to PDF (server-side)
* ``POST /run``                       -> start the pipeline for a company (background)
* ``GET  /status/{slug}``             -> progress: state, elapsed, live log lines

``{slug}`` is the filesystem slug (e.g. ``bharath_foods_limited``); a company
name is accepted too and is slugified automatically.

Run (from the project root, with the venv active)::

    uvicorn api:app --reload --port 8000

CORS is opened for the Next.js dev server on http://localhost:3000. The
Streamlit app (``app.py``) is untouched and remains a working fallback.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from scd import config

logger = logging.getLogger("scd.api")

MAX_LOG_LINES = 4000
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


# ---------------------------------------------------------------------------
# Live log capture (per background run)
# ---------------------------------------------------------------------------
class _RunLogHandler(logging.Handler):
    """Append formatted log records to the log buffer of the running company.

    Uses a thread-local "current company" so concurrent runs (one per thread)
    never cross-contaminate each other's logs. Never prints the API key.
    """

    def __init__(self) -> None:
        super().__init__()
        self._tls = threading.local()

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - simple IO
        company = getattr(self._tls, "company", None)
        if not company:
            return
        entry = _STATUS.get(company)
        if entry is None:
            return
        try:
            entry["logs"].append(self.format(record))
        except Exception:
            pass

    @property
    def current_company(self) -> Optional[str]:
        return getattr(self._tls, "company", None)

    @current_company.setter
    def current_company(self, value: Optional[str]) -> None:
        self._tls.company = value


_log_handler = _RunLogHandler()
_log_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
)

# Attach to the "scd" logger tree so every pipeline module is captured, while
# still propagating to the root logger for server-console output.
_scd_logger = logging.getLogger("scd")
if _log_handler not in _scd_logger.handlers:
    _scd_logger.setLevel(logging.INFO)
    _scd_logger.addHandler(_log_handler)
    _scd_logger.propagate = True

# In-memory run status keyed by company slug.
_STATUS: dict[str, dict] = {}
_STATUS_LOCK = threading.Lock()


def _new_status(company: str, slug: str) -> dict:
    return {
        "company": company,
        "slug": slug,
        "state": "running",            # running | done | error
        "logs": deque(maxlen=MAX_LOG_LINES),
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
        "report_path": None,
    }


def _report_exists(slug: str) -> bool:
    return (config.OUT_DIR / slug / "report.json").exists()


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------
app = FastAPI(title="Silent Change Detector API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    company: str = Field(..., description="Full company name, e.g. 'ITC Limited'.")
    demo: bool = Field(False, description="Offline demo: synthetic PDFs + scripted LLM (no API key).")
    max_chunks: Optional[int] = Field(None, description="Only process the first N chunks (faster).")


def _key_present() -> bool:
    import os

    raw = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    return bool(raw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_report(slug: str) -> dict:
    """Load a finished report.json by slug; raise 404 if it is missing."""
    path = config.OUT_DIR / slug / "report.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No finished report for '{slug}'. Run the pipeline first (POST /run).",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt report.json for '{slug}': {exc}")


def _list_company_slugs() -> list[str]:
    """Slugs that contain a finished report.json."""
    if not config.OUT_DIR.exists():
        return []
    return sorted(
        d.name for d in config.OUT_DIR.iterdir() if d.is_dir() and (d / "report.json").exists()
    )


def _public_status(slug: str) -> dict:
    """A JSON-safe snapshot of a run's status (no secrets, bounded logs)."""
    with _STATUS_LOCK:
        entry = _STATUS.get(slug)
        if entry is None:
            # Never started via the API: report presence alone drives the UI.
            return {
                "company": slug,
                "slug": slug,
                "state": "done" if _report_exists(slug) else "idle",
                "report_exists": _report_exists(slug),
                "logs": [],
                "started_at": None,
                "finished_at": None,
                "elapsed_seconds": None,
                "error": None,
            }
        logs = list(entry["logs"])
        started = entry["started_at"]
        finished = entry["finished_at"] or time.time()
        return {
            "company": entry["company"],
            "slug": entry["slug"],
            "state": entry["state"],
            "report_exists": _report_exists(slug),
            "logs": logs[-400:],  # return the tail; full buffer is bounded server-side
            "started_at": started,
            "finished_at": entry["finished_at"],
            "elapsed_seconds": round(finished - started, 1) if started else None,
            "error": entry["error"],
        }


def _run_worker(company: str, slug: str, demo: bool, max_chunks: Optional[int]) -> None:
    """Background worker: run the pipeline and capture its logs + outcome."""
    import run as run_mod  # imported here so a bad run never blocks API startup

    with _STATUS_LOCK:
        entry = _new_status(company, slug)
        _STATUS[slug] = entry

    # Route this thread's scd.* log records into this company's buffer.
    prev = _log_handler.current_company
    _log_handler.current_company = slug
    try:
        report = run_mod.run_pipeline(company, use_demo=demo, max_chunks=max_chunks)
        entry["state"] = "done"
        entry["report_path"] = report.get("_meta", {}).get("report_path")
    except SystemExit as exc:  # run_pipeline signals failures via SystemExit
        entry["state"] = "error"
        entry["error"] = f"Pipeline exited with code {exc.code}. See logs for the failing stage."
    except Exception as exc:  # noqa: BLE001 - surface anything, never crash the server
        entry["state"] = "error"
        entry["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("Run failed for %s: %s", company, traceback.format_exc())
    finally:
        entry["finished_at"] = time.time()
        _log_handler.current_company = prev


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    """Liveness + whether a Gemini key is configured (boolean only, never the key)."""
    return {
        "status": "ok",
        "api_key_present": _key_present(),
        "companies": len(_list_company_slugs()),
    }


@app.get("/companies")
def companies() -> dict:
    """List companies that already have a finished report.json."""
    slugs = _list_company_slugs()
    items = []
    for slug in slugs:
        path = config.OUT_DIR / slug / "report.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append(
                {
                    "slug": slug,
                    # Display names: prefer report._meta.company, then top-level
                    # "company" if it is a real name; the FS slug is last resort.
                    "company": (
                        (data.get("_meta") or {}).get("company")
                        or (data.get("company") if isinstance(data.get("company"), str) else None)
                        or slug
                    ),
                    # The frontend badge reads evidence_strength.rating, so hand
                    # it the real object (or "" for legacy reports) -- the old
                    # code coerced the dict to a string, which broke the badge.
                    "evidence_strength": data.get("evidence_strength") or "",
                }
            )
        except Exception:
            items.append({"slug": slug, "company": slug, "evidence_strength": ""})
    return {"companies": items, "count": len(items)}


@app.get("/report/{slug}")
def get_report(slug: str) -> dict:
    """Return the exact report.json for ``slug`` (company names are slugified)."""
    return _load_report(config.slugify(slug))


@app.get("/report/{slug}/pdf")
def get_report_pdf(slug: str) -> Response:
    """Render a finished report to PDF (server-side) and return the bytes."""
    slug = config.slugify(slug)
    report = _load_report(slug)
    company = report.get("company") or slug
    try:
        from report import build_pdf_bytes

        pdf = build_pdf_bytes(report, company)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF export failed: {exc}")
    filename = f"{slug}_silent_change_report.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/run", status_code=202)
def start_run(req: RunRequest) -> dict:
    """Start the pipeline for ``req.company`` in a background thread."""
    company = req.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="company must be a non-empty string.")
    slug = config.slugify(company)

    with _STATUS_LOCK:
        existing = _STATUS.get(slug)
        if existing and existing["state"] == "running":
            return {
                "slug": slug,
                "state": "running",
                "message": "A run for this company is already in progress.",
            }

    # Reject empty-input runs early with a clear message (no PDFs to process).
    manual = config.company_manual_dir(company)
    if not req.demo and not (manual.exists() and any(manual.rglob("*.pdf"))):
        return JSONResponse(
            status_code=400,
            content={
                "slug": slug,
                "state": "error",
                "error": (
                    f"No PDFs found in {manual}. Drop the company's filings there, "
                    "or start a demo run with demo=true."
                ),
            },
        )

    thread = threading.Thread(
        target=_run_worker,
        args=(company, slug, req.demo, req.max_chunks),
        name=f"scd-run-{slug}",
        daemon=True,
    )
    thread.start()
    return {"slug": slug, "state": "running", "message": "Pipeline started.", "company": company}


@app.get("/status/{slug}")
def get_status(slug: str) -> dict:
    """Progress for a run: state, elapsed time and the live log tail."""
    return _public_status(config.slugify(slug))


@app.exception_handler(Exception)
def _unhandled(exc: Exception):  # pragma: no cover - defensive
    """Last-resort handler so the browser gets JSON, not an HTML stack trace."""
    logger.error("Unhandled API error: %s", traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
