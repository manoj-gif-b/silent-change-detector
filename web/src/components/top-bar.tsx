"use client";

// Sticky top bar: brand, company selector, and the Export PDF button.
// Export uses the server-rendered PDF endpoint (the API key never leaves the
// server); Streamlit remains an independent fallback for the same report.

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Radar, Download, ChevronDown, Check } from "lucide-react";

import { reportPdfUrl } from "@/lib/api";
import { companyName } from "@/lib/helpers";
import type { CompanySummary, Report } from "@/lib/types";

interface Props {
  companies: CompanySummary[];
  slug: string | null;
  onSelect: (slug: string) => void;
  onRefresh: () => Promise<unknown> | void;
  report: Report | null;
}

export default function TopBar({ companies, slug, onSelect, onRefresh, report }: Props) {
  const [open, setOpen] = useState(false);

  // Close the dropdown when clicking outside.
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  const active = companies.find((c) => c.slug === slug) || null;

  return (
    <header className="sticky top-0 z-30 border-b border-white/5 bg-ink-950/80 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-6xl items-center gap-4 px-5 py-3 sm:px-8">
        {/* Brand */}
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent-500/15 text-accent-300 ring-1 ring-accent-500/30">
            <Radar className="h-4 w-4" />
          </span>
          <div className="leading-tight">
            <p className="text-sm font-semibold text-mist-100">Silent Change Detector</p>
            <p className="text-[11px] text-mist-500">Finding the Next Signal</p>
          </div>
        </div>

        <div className="flex-1" />

        {/* Company selector */}
        <div className="relative" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-2 rounded-lg border border-white/10 bg-ink-850/70 px-3 py-2 text-sm text-mist-100 transition-colors hover:border-accent-500/40"
            aria-haspopup="listbox"
            aria-expanded={open}
          >
            <span className="max-w-[46vw] truncate sm:max-w-[320px]">
              {active ? active.company : "Select company"}
            </span>
            <ChevronDown className={`h-4 w-4 text-mist-400 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>

          {open && (
            <motion.ul
              role="listbox"
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              className="panel absolute right-0 top-11 z-40 max-h-80 w-72 overflow-y-auto p-1"
            >
              {companies.length === 0 && (
                <li className="px-3 py-2 text-sm text-mist-500">No reports yet</li>
              )}
              {companies.map((c) => (
                <li key={c.slug}>
                  <button
                    role="option"
                    aria-selected={c.slug === slug}
                    onClick={() => {
                      onSelect(c.slug);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                      c.slug === slug
                        ? "bg-accent-500/10 text-accent-200"
                        : "text-mist-300 hover:bg-white/5"
                    }`}
                  >
                    <span className="truncate">{c.company}</span>
                    {c.slug === slug && <Check className="h-4 w-4 shrink-0" />}
                  </button>
                </li>
              ))}
            </motion.ul>
          )}
        </div>

        {/* Export PDF */}
        <button
          disabled={!report || !slug}
          onClick={() => {
            if (!slug) return;
            window.open(reportPdfUrl(slug), "_blank", "noopener");
            void onRefresh();
          }}
          className="flex items-center gap-2 rounded-lg bg-accent-500/15 px-3 py-2 text-sm font-medium text-accent-200 ring-1 ring-accent-500/30 transition-colors hover:bg-accent-500/25 disabled:cursor-not-allowed disabled:opacity-40"
          title={
            report
              ? `Export ${companyName(report, slug ?? "")} as PDF`
              : "Run a company first"
          }
        >
          <Download className="h-4 w-4" />
          <span className="hidden sm:inline">Export PDF</span>
        </button>
      </div>
    </header>
  );
}
