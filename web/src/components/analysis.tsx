"use client";

// Section 5 -- Analysis: "Why it matters" bullets, the Counterargument and the
// Falsification Condition in clear panels, plus the red-team checks that fed the
// Evidence Strength rating. Mirrors the report.json schema exactly.

import { motion } from "framer-motion";
import { AlertTriangle, Target, ListChecks, Check, X } from "lucide-react";

import { normalizeStrength, strengthClasses } from "@/lib/helpers";
import type { Report } from "@/lib/types";

const fadeUp = {
  initial: { opacity: 0, y: 16 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-80px" },
  transition: { duration: 0.5 },
};

export default function Analysis({ report }: { report: Report }) {
  const rating = normalizeStrength(report.evidence_strength?.rating);
  const sc = strengthClasses(rating);
  const checks = report._meta?.redteam_checks ?? [];

  return (
    <section className="space-y-8">
      <motion.div {...fadeUp}>
        <p className="section-title mb-4">Why it matters</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {report.why_it_matters.map((item, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.4, delay: i * 0.06 }}
              className="panel flex items-start gap-3 p-4"
            >
              <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md bg-accent-500/15 text-[11px] font-bold text-accent-300 ring-1 ring-accent-500/30">
                {i + 1}
              </span>
              <p className="text-sm leading-relaxed text-mist-300">{item}</p>
            </motion.div>
          ))}
        </div>
      </motion.div>

      <div className="grid gap-6 lg:grid-cols-2">
        <motion.div {...fadeUp} className="panel border-l-2 border-strength-moderate/50 p-5">
          <div className="mb-3 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-strength-moderate" />
            <p className="section-title text-strength-moderate">Counterargument</p>
          </div>
          <p className="text-sm leading-relaxed text-mist-300">{report.counterargument}</p>
        </motion.div>

        <motion.div {...fadeUp} className="panel border-l-2 border-accent-500/50 p-5">
          <div className="mb-3 flex items-center gap-2">
            <Target className="h-4 w-4 text-accent-300" />
            <p className="section-title text-accent-300">Falsification condition</p>
          </div>
          <p className="text-sm leading-relaxed text-mist-300">{report.falsification_condition}</p>
        </motion.div>
      </div>

      <motion.div {...fadeUp}>
        <div className="mb-4 flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-mist-400" />
          <p className="section-title">Red-team checks</p>
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-3 lg:col-span-2">
            {checks.length === 0 && (
              <p className="panel p-4 text-sm text-mist-500">No red-team checks recorded.</p>
            )}
            {checks.map((c, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: -8 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ duration: 0.4, delay: i * 0.05 }}
                className="panel flex items-start gap-3 p-4"
              >
                <span
                  className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                    c.supported
                      ? "bg-strength-weak/15 text-strength-weak"
                      : "bg-strength-strong/15 text-strength-strong"
                  }`}
                >
                  {c.supported ? <X className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />}
                </span>
                <div>
                  <p className="text-sm font-medium text-mist-100">
                    {c.name.replace(/_/g, " ")}
                    <span className="ml-2 text-[11px] font-normal text-mist-500">
                      {c.supported ? "raised a concern" : "did not overturn"}
                    </span>
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-mist-400">{c.reasoning}</p>
                </div>
              </motion.div>
            ))}
          </div>

          <div className={`panel h-fit p-5 ring-1 ${sc.border}`}>
            <div className="mb-3 flex items-center gap-2">
              <span className={`h-2.5 w-2.5 rounded-full ${sc.dot}`} />
              <span className={`text-sm font-semibold ${sc.text}`}>{rating}</span>
              {report.evidence_strength?.checks_survived && (
                <span className="ml-auto rounded-full bg-black/20 px-2 py-0.5 text-[11px] text-mist-300">
                  {report.evidence_strength.checks_survived} survived
                </span>
              )}
            </div>
            <p className="text-sm leading-relaxed text-mist-300">
              {report.evidence_strength?.reasoning}
            </p>
            {report.evidence_strength?.downgrade_note && (
              <p className="mt-3 rounded-lg border border-strength-weak/30 bg-strength-weak/10 p-3 text-xs text-strength-weak">
                Downgraded: {report.evidence_strength.downgrade_note}
              </p>
            )}
          </div>
        </div>
      </motion.div>
    </section>
  );
}
