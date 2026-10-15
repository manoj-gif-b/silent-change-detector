"use client";

// Section 6 -- Run on a new company: POST /run to start the pipeline in the
// background, then poll GET /status/{company} for live progress logs.
// The API key stays server-side; this only drives the run and shows progress.

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Play, Loader2, CheckCircle2, AlertOctagon, Terminal } from "lucide-react";

import { fetchStatus, startRun } from "@/lib/api";
import { slugify } from "@/lib/helpers";
import type { RunState, RunStatus } from "@/lib/types";

const POLL_MS = 1200;

export default function RunBox({
  onStarted,
  onFinished,
}: {
  onStarted?: (slug: string) => void;
  onFinished: (slug: string) => void;
}) {
  const [company, setCompany] = useState("");
  const [state, setState] = useState<RunState>("idle");
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  // Keep the log pinned to the bottom as lines arrive.
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [status?.logs?.length]);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => stopPolling, []);

  const start = async () => {
    const trimmed = company.trim();
    if (!trimmed) return;
    setError(null);
    setState("running");
    setStatus(null);

    try {
      const slug = slugify(trimmed);
      const accepted = await startRun({ company: trimmed });
      const runSlug = accepted.slug || slug;
      onStarted?.(runSlug);

      // Poll status until done or error.
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const s = await fetchStatus(runSlug);
          setStatus(s);
          if (s.state === "done" || s.state === "error") {
            stopPolling();
            setState(s.state);
            if (s.error) {
              setError(s.error);
            } else if (s.state === "done") {
              // Switch only now: the finished report.json exists, so the page
              // loads a real report instead of a missing-report error.
              onFinished(runSlug);
            }
          }
        } catch (e) {
          stopPolling();
          setState("error");
          setError(e instanceof Error ? e.message : String(e));
        }
      }, POLL_MS);
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const busy = state === "running";

  return (
    <section className="panel p-6">
      <div className="mb-4 flex items-center gap-2">
        <Terminal className="h-4 w-4 text-accent-300" />
        <p className="section-title">Run on a new company</p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        <input
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !busy && start()}
          placeholder="e.g. Hindustan Unilever Limited"
          disabled={busy}
          className="flex-1 rounded-lg border border-white/10 bg-ink-800/70 px-4 py-2.5 text-sm text-mist-100 placeholder:text-mist-500 outline-none transition-colors focus:border-accent-500/50 disabled:opacity-60"
        />
        <button
          onClick={start}
          disabled={busy || !company.trim()}
          className="flex items-center justify-center gap-2 rounded-lg bg-accent-500/15 px-5 py-2.5 text-sm font-medium text-accent-200 ring-1 ring-accent-500/30 transition-colors hover:bg-accent-500/25 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Running…
            </>
          ) : (
            <>
              <Play className="h-4 w-4" /> Start run
            </>
          )}
        </button>
      </div>

      <p className="mt-2 text-xs text-mist-500">
        Runs the full pipeline server-side (ingest → ledger → detectors →
        hypothesis → red-team). The API key never leaves the server.
      </p>

      {/* Status / live logs */}
      <AnimatePresence>
        {(busy || state === "done" || state === "error") && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-4 overflow-hidden"
          >
            <div className="flex items-center gap-2 text-sm">
              {busy && <Loader2 className="h-4 w-4 animate-spin text-accent-300" />}
              {state === "done" && <CheckCircle2 className="h-4 w-4 text-strength-strong" />}
              {state === "error" && <AlertOctagon className="h-4 w-4 text-strength-weak" />}
              <span className="text-mist-200">
                {busy
                  ? `Running${status?.elapsed_seconds != null ? ` (${Math.round(status.elapsed_seconds)}s)` : ""}…`
                  : state === "done"
                    ? "Report ready"
                    : "Run failed"}
              </span>
            </div>

            {status?.logs && status.logs.length > 0 && (
              <div
                ref={logRef}
                className="mt-3 max-h-52 overflow-y-auto rounded-lg border border-white/5 bg-ink-950/80 p-3 font-mono text-[11px] leading-relaxed text-mist-400"
              >
                {status.logs.map((line, i) => (
                  <div key={i}>{line}</div>
                ))}
              </div>
            )}

            {error && (
              <p className="mt-3 rounded-lg border border-strength-weak/30 bg-strength-weak/10 p-3 text-xs text-strength-weak">
                {error}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
