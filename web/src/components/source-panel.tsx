"use client";

// Slide-in side panel showing the source behind any clicked claim:
// document name, page, period, the exact verbatim quote, and a link to the
// source. Opened from anywhere via the shared EvidenceProvider.

import { AnimatePresence, motion } from "framer-motion";
import { X, FileText, Hash, CalendarDays, ExternalLink } from "lucide-react";

import { useEvidence } from "./evidence-context";
import { isWebLink, shortLink } from "@/lib/helpers";

export default function SourcePanel() {
  const { selected, clear } = useEvidence();

  return (
    <AnimatePresence>
      {selected && (
        <>
          {/* Scrim */}
          <motion.button
            aria-label="Close source panel"
            onClick={clear}
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          />
          <motion.aside
            role="dialog"
            aria-label="Evidence source"
            className="fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l border-white/10 bg-ink-900/95 shadow-panel backdrop-blur-md"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
          >
            <header className="flex items-start justify-between gap-4 border-b border-white/5 p-5">
              <div>
                <p className="section-title">Source evidence</p>
                {selected.label && (
                  <h3 className="mt-1 text-base font-semibold leading-snug text-mist-100">
                    {selected.label}
                  </h3>
                )}
              </div>
              <button
                onClick={clear}
                className="rounded-lg border border-white/10 p-1.5 text-mist-400 transition-colors hover:border-white/20 hover:text-mist-100"
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </header>

            <div className="flex-1 space-y-6 overflow-y-auto p-5">
              {/* Provenance metadata */}
              <dl className="space-y-3">
                <MetaRow icon={<FileText className="h-4 w-4" />} label="Document">
                  <span className="font-mono text-sm text-mist-100">{selected.doc}</span>
                </MetaRow>
                <div className="flex gap-6">
                  <MetaRow icon={<Hash className="h-4 w-4" />} label="Page">
                    <span className="text-mist-100">{selected.page ?? "—"}</span>
                  </MetaRow>
                  {selected.period && (
                    <MetaRow icon={<CalendarDays className="h-4 w-4" />} label="Period">
                      <span className="text-mist-100">{selected.period}</span>
                    </MetaRow>
                  )}
                </div>
              </dl>

              {/* Verbatim quote */}
              <div>
                <p className="section-title mb-2">Verbatim quote</p>
                <blockquote className="quote">{selected.quote}</blockquote>
              </div>

              {/* Source link */}
              {selected.link && (
                <div>
                  <p className="section-title mb-2">Open source</p>
                  {isWebLink(selected.link) ? (
                    <a
                      href={selected.link}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-1.5 text-sm text-accent-300 underline decoration-dotted underline-offset-4 hover:text-accent-400"
                    >
                      <ExternalLink className="h-4 w-4" />
                      {shortLink(selected.link)}
                    </a>
                  ) : (
                    <p className="font-mono text-xs text-mist-500">
                      Local file: {shortLink(selected.link)}
                    </p>
                  )}
                </div>
              )}
            </div>

            <footer className="border-t border-white/5 p-4 text-[11px] text-mist-500">
              Every claim in this report links back to a document, page and quote.
            </footer>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

function MetaRow({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-mist-500">{icon}</span>
      <dt className="w-16 shrink-0 text-xs text-mist-500">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
