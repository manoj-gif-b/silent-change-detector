"use client";

// Root page: loads the company list + report from the FastAPI backend and
// composes all sections. All clickable claims route through the shared
// EvidenceProvider -> SourcePanel so every number traces to its source.

import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

import { EvidenceProvider } from "@/components/evidence-context";
import TopBar from "@/components/top-bar";
import Hero from "@/components/hero";
import EvidenceGraph from "@/components/evidence-graph";
import Timeline from "@/components/timeline";
import Numbers from "@/components/numbers";
import Analysis from "@/components/analysis";
import RunBox from "@/components/run-box";
import SourcePanel from "@/components/source-panel";
import ApiHealth from "@/components/api-health";

import { fetchCompanies, fetchReport } from "@/lib/api";
import { companyName } from "@/lib/helpers";
import type { CompaniesResponse, CompanySummary, Report } from "@/lib/types";

export default function Home() {
  const [companies, setCompanies] = useState<CompanySummary[]>([]);
  const [companiesError, setCompaniesError] = useState<string | null>(null);
  const [slug, setSlug] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  const refreshCompanies = useCallback(async () => {
    try {
      const res: CompaniesResponse = await fetchCompanies();
      setCompanies(res.companies);
      setCompaniesError(null);
      return res.companies;
    } catch (e) {
      setCompaniesError(e instanceof Error ? e.message : String(e));
      return [] as CompanySummary[];
    }
  }, []);

  // Initial load: list companies, auto-select the first.
  useEffect(() => {
    (async () => {
      const list = await refreshCompanies();
      if (list.length > 0) setSlug(list[0].slug);
    })();
  }, [refreshCompanies]);

  // Load the report whenever the selected company changes.
  useEffect(() => {
    if (!slug) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setLoadingReport(true);
    setReportError(null);
    fetchReport(slug)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((e) => {
        if (!cancelled) {
          setReport(null);
          setReportError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingReport(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const displayName = useMemo(
    () => companyName(report, slug ?? ""),
    [report, slug],
  );

  return (
    <EvidenceProvider>
      <div className="relative min-h-screen">
        <ApiHealth />
        <TopBar
          companies={companies}
          slug={slug}
          onSelect={setSlug}
          onRefresh={refreshCompanies}
          report={report}
        />

        <main className="mx-auto w-full max-w-6xl px-5 pb-32 pt-8 sm:px-8">
          {companiesError && !report && (
            <EmptyState title="Can't reach the API" body={companiesError} />
          )}

          {loadingReport && !report && (
            <div className="flex items-center justify-center py-40 text-mist-400">
              <Spinner label={`Loading ${displayName}…`} />
            </div>
          )}

          {reportError && !report && (
            <EmptyState
              title={`No finished report for “${displayName}”`}
              body={reportError}
            />
          )}

          {report && (
            <div className="space-y-16 sm:space-y-24">
              <Hero company={displayName} report={report} />
              <EvidenceGraph report={report} />
              <Timeline report={report} />
              <Numbers report={report} />
              <Analysis report={report} />
              <RunBox
                onFinished={async (newSlug) => {
                  await refreshCompanies();
                  setSlug(newSlug);
                }}
              />
              <Footer />
            </div>
          )}
        </main>

        <SourcePanel />
      </div>
    </EvidenceProvider>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="panel mx-auto mt-16 max-w-xl p-8 text-center"
    >
      <h2 className="text-lg font-semibold text-mist-100">{title}</h2>
      <p className="mt-2 text-sm text-mist-400">{body}</p>
      <p className="mt-4 text-xs text-mist-500">
        Start the API with{" "}
        <code className="rounded bg-ink-800 px-1 py-0.5 font-mono text-accent-300">
          uvicorn api:app --reload
        </code>
        , or run the pipeline offline, then refresh.
      </p>
    </motion.div>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-4">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-mist-600 border-t-accent-400" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

function Footer() {
  return (
    <footer className="animate-fade-up border-t border-white/5 pt-8 text-xs text-mist-500">
      <p>
        Silent Change Detector — ATMOS 2026, “Finding the Next Signal”. Every
        figure shown traces to a public document, page and verbatim quote. This
        tool is a research aid, not investment advice.
      </p>
    </footer>
  );
}
