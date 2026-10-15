"use client";

// Section 1 -- Hero: the company, the single-sentence Discovery, and the
// Evidence Strength badge. Hovering the badge reveals the reasoning + how many
// red-team checks survived. The detector chip links to its source section.

import { motion } from "framer-motion";
import { Sparkles, ShieldCheck } from "lucide-react";

import { normalizeStrength, strengthClasses } from "@/lib/helpers";
import type { EvidenceStrengthRating, Report } from "@/lib/types";

export default function Hero({ company, report }: { company: string; report: Report }) {
  const rating: EvidenceStrengthRating = normalizeStrength(report.evidence_strength?.rating);
  const sc = strengthClasses(rating);
  const reasoning = report.evidence_strength?.reasoning;
  const survived = report.evidence_strength?.checks_survived;
  const downgrade = report.evidence_strength?.downgrade_note;
  const detector = report._meta?.detector;

  return (
    <section className="animate-fade-up pt-4">
      <div className="flex flex-wrap items-center gap-3">
        <p className="section-title">Discovery</p>
        {detector && (
          <span className="rounded-full border border-white/10 bg-ink-850/70 px-2.5 py-0.5 font-mono text-[11px] text-mist-400">
            {detector.replace(/_/g, " ")}
          </span>
        )}
      </div>

      <h1 className="mt-3 text-4xl font-bold leading-tight tracking-tight text-mist-100 sm:text-5xl">
        {company}
      </h1>

      <motion.p
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.1 }}
        className="mt-5 max-w-4xl text-lg leading-relaxed text-mist-300 sm:text-xl"
      >
        <Sparkles className="mr-2 inline h-5 w-5 -translate-y-px text-accent-400" />
        {report.discovery}
      </motion.p>

      {/* Evidence strength badge with hover reasoning */}
      <div className="group relative mt-8 inline-flex">
        <div
          className={`flex cursor-default items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm font-medium ${sc.border} ${sc.bg} ${sc.text}`}
          tabIndex={0}
        >
          <span className={`h-2 w-2 rounded-full ${sc.dot}`} />
          <ShieldCheck className="h-4 w-4" />
          Evidence: {rating}
          {survived && (
            <span className="ml-1 rounded-full bg-black/20 px-2 py-0.5 text-[11px] text-mist-300">
              {survived} checks survived
            </span>
          )}
        </div>

        {/* Hover/focus tooltip */}
        <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 w-80 origin-top scale-95 rounded-xl border border-white/10 bg-ink-900/95 p-4 text-sm opacity-0 shadow-panel backdrop-blur-md transition-all duration-150 group-hover:scale-100 group-hover:opacity-100 group-focus-within:scale-100 group-focus-within:opacity-100">
          <p className="section-title mb-1.5">Why {rating}?</p>
          <p className="leading-relaxed text-mist-300">{reasoning || "No reasoning provided."}</p>
          {downgrade && (
            <p className="mt-2 rounded-lg border border-strength-weak/30 bg-strength-weak/10 px-2.5 py-1.5 text-xs text-strength-weak">
              Downgraded: {downgrade}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
