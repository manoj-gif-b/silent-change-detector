"use client";

// Section 3 -- Timeline: how the change emerged over time, animated on scroll.
// The metric value is plotted as a spine of dots; the point where disclosure
// stops (if present) is marked. Clicking any point opens its source quote.

import { useMemo } from "react";
import { motion } from "framer-motion";
import { GitCommitVertical } from "lucide-react";

import { useEvidence } from "./evidence-context";
import { num } from "@/lib/helpers";
import type { Report } from "@/lib/types";

export default function Timeline({ report }: { report: Report }) {
  const { selectEvidence } = useEvidence();
  const points = report.timeline ?? [];
  const trail = report.evidence_trail ?? [];

  const { min, max, firstMissing } = useMemo(() => {
    const vals = points.map((p) => num(p.value));
    const lo = vals.length ? Math.min(...vals) : 0;
    const hi = vals.length ? Math.max(...vals) : 1;
    return { min: lo, max: hi, firstMissing: report.numbers?.first_missing_period ?? null };
  }, [points, report.numbers?.first_missing_period]);

  if (points.length === 0) return null;
  const span = max - min || 1;

  const openSource = (idx: number) => {
    // Prefer a trail item matching this period; fall back to the first.
    const match = trail.find((t) => t.period === points[idx].period) ?? trail[0];
    if (!match) return;
    selectEvidence({
      kind: "trail",
      doc: match.document,
      page: match.page,
      quote: match.quote,
      period: match.period ?? points[idx].period,
      link: match.link,
      label: `${points[idx].metric}: ${points[idx].value}`,
    });
  };

  return (
    <section className="space-y-6">
      <div className="flex items-center gap-2">
        <GitCommitVertical className="h-4 w-4 text-mist-400" />
        <p className="section-title">How the change emerged</p>
      </div>

      <div className="panel relative overflow-hidden p-6 sm:p-8">
        {/* The spine */}
        <div className="relative">
          <div className="absolute left-2 top-0 h-full w-px bg-gradient-to-b from-accent-500/60 via-mist-600/40 to-transparent" />

          <ol className="space-y-5">
            {points.map((p, i) => {
              const v = num(p.value);
              const pct = ((v - min) / span) * 100;
              const isGap = firstMissing === p.period;
              return (
                <motion.li
                  key={`${p.period}-${i}`}
                  initial={{ opacity: 0, x: -12 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true, margin: "-60px" }}
                  transition={{ duration: 0.4, delay: Math.min(i * 0.04, 0.5) }}
                  className="relative pl-10"
                >
                  {/* Dot on the spine, positioned by value */}
                  <span
                    className="absolute left-0 top-1.5 grid h-4 w-4 place-items-center"
                    aria-hidden
                  >
                    <span
                      className={`h-2.5 w-2.5 rounded-full ring-2 ring-ink-900 ${
                        isGap ? "bg-strength-weak" : "bg-accent-400"
                      }`}
                      style={{ transform: `translateX(${(pct / 100) * 6}px)` }}
                    />
                  </span>

                  <button
                    onClick={() => openSource(i)}
                    className="group flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 rounded-md px-2 py-1 text-left transition-colors hover:bg-white/5"
                    title="View the source behind this point"
                  >
                    <span className="font-mono text-xs text-mist-500">{p.period}</span>
                    <span className="text-sm font-medium text-mist-100 group-hover:text-accent-300">
                      {p.metric}
                    </span>
                    <span
                      className={`text-sm font-semibold ${
                        isGap ? "text-strength-weak" : "text-accent-300"
                      }`}
                    >
                      {v}
                      {typeof p.value === "number" && Number.isFinite(p.value) ? "%" : ""}
                    </span>
                    <span className="ml-auto text-[11px] text-mist-600 opacity-0 transition-opacity group-hover:opacity-100">
                      view source →
                    </span>
                  </button>
                </motion.li>
              );
            })}

            {/* Marker for where disclosure stopped */}
            {firstMissing && (
              <motion.li
                initial={{ opacity: 0 }}
                whileInView={{ opacity: 1 }}
                viewport={{ once: true }}
                transition={{ duration: 0.5 }}
                className="relative pl-10"
              >
                <span className="absolute left-0 top-1.5 grid h-4 w-4 place-items-center" aria-hidden>
                  <span className="h-2.5 w-2.5 rounded-full bg-strength-weak/40 ring-2 ring-ink-900" />
                </span>
                <div className="rounded-md border border-dashed border-strength-weak/40 bg-strength-weak/5 px-3 py-2">
                  <span className="font-mono text-xs text-strength-weak">{firstMissing}</span>
                  <span className="ml-2 text-sm text-mist-300">
                    metric no longer disclosed — the silent change
                  </span>
                </div>
              </motion.li>
            )}
          </ol>
        </div>
      </div>
    </section>
  );
}
