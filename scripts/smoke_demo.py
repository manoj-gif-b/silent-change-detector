"""Offline end-to-end smoke test of the whole pipeline using the demo fixture.

Run with:  python scripts/smoke_demo.py
Exercises ingest -> ledger -> detectors (and later hypothesis/redteam/report)
using synthetic data + a scripted LLM, so it needs no API key.
"""

from __future__ import annotations

import logging
import os
import sys

# Allow running as `python scripts/smoke_demo.py` from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SCD_LLM_PROVIDER", "mock")

from scd import demo  # noqa: E402
import ledger, detectors  # noqa: E402
import ingest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    demo.generate_demo_pdfs()
    manifest = ingest.ingest_company(demo.DEMO_COMPANY)
    print(f"[ingest] docs={manifest['n_documents']} chunks={manifest['n_chunks']}")

    llm = demo.scripted_llm()
    df = ledger.run(demo.DEMO_COMPANY, llm=llm)
    print(f"[ledger] rows={len(df)}")
    if not df.empty:
        print(df[["period", "name", "value"]].head(12).to_string(index=False))

    anoms = detectors.run_all(df, demo.DEMO_COMPANY, llm=llm)
    print(f"[detectors] anomalies={len(anoms)}")
    for a in anoms[:6]:
        print(f"  [{a['detector']}] score={a['score']} {a['title']}")


if __name__ == "__main__":
    main()
