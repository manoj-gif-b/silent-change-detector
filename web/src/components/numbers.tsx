"use client";

// Section 4 -- Key numbers: clean cards for the figures that support the
// finding, plus a small line chart of the metric across time (recharts).
// Only the numbers the report actually contains are shown (never invented).
// Each card traces to a source when clicked.

import { useMemo } from "react";
import { motion } from "framer-motion";
import { BarChart3, Hash } from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

import { useEvidence } from "./evidence-context";
import { num } from "@/lib/helpers";
import type { Report } from "@/lib/types";

interface Card {
  label: string;
  value: string;
  hint?: string;
}

/** Turn the free-form `numbers` block into display cards (schema-driven). */
function buildCards(report: Report): Card[] {
  const n = report.numbers ?? {};
  const cards: Card[] = [];

  if (typeof n.kpi === "string") cards.push({ label: "Metric", value: n.kpi });
  if (typeof n.periods_reported === "number")
    cards.push({ label: "Periods reported", value: String(n.periods_reported) });
  if (typeof n.last_reported_period === "string")
    cards.push({ label: "Last disclosed", value: n.last_reported_period });
  if (typeof n.first_missing_period === "string")
    cards.push({ label: "Absent since", value: n.first_missing_period, hint: "not disclosed" });
  if (typeof n.trailing_missing_periods === "number")
    cards.push({
      label: "Periods missing",
      value: String(n.trailing_missing_periods),
      hint: "in a row",
    });
  if (typeof n.redefined === "boolean")
    cards.push({ label: "Definition changed", value: n.redefined ? "Yes" : "No" });

  // Any other numeric keys the detector added (e.g. narrative/peer extras).
  for (const [k, v] of Object.entries(n)) {
    if (cards.some((c) => c.label === k)) continue;
    if (typeof v === "number" && cards.length < 8) {
      cards.push({ label: k.replace(/_/g, " "), value: String(v) });
    }
  }
  return cards;
}

export default function Numbers({ report }: { report: Report }) {
  const { selectEvidence } = useEvidence();
  const cards = useMemo(() => buildCards(report), [report]);
  const trail = report.evidence_trail ?? [];

  const chartData = useMemo(
    () =>
      report.timeline.map((t) => ({
        period: t.period,
        value: num(t.value),
        metric: t.metric,
      })),
    [report.timeline],
  );
  const metric = report.timeline[0]?.metric ?? "value";
  const primaryEvidence = trail[0];

  return (
    <section className="space-y-6">
      <div className="flex items-center gap-2">
        <Hash className="h-4 w-4 text-mist-400" />
        <p className="section-title">Key numbers</p>
        <span className="ml-auto text-[11px] text-mist-500">
          only figures that support the finding
        </span>
      </div>

      {/* Number cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {cards.map((card, i) => (
          <motion.button
            key={i}
            initial={{ opacity: 0, y: 10 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.35, delay: i * 0.05 }}
            onClick={() =>
              primaryEvidence &&
              selectEvidence({
                kind: "trail",
                doc: primaryEvidence.document,
                page: primaryEvidence.page,
                quote: primaryEvidence.quote,
                period: primaryEvidence.period,
                link: primaryEvidence.link,
                label: card.label,
              })
            }
            disabled={!primaryEvidence}
            className="panel panel-hover p-4 text-left"
            title={primaryEvidence ? "Click to view the source behind this figure" : undefined}
          >
            <p className="text-[11px] uppercase tracking-wide text-mist-500">{card.label}</p>
            <p className="mt-1 text-2xl font-semibold text-mist-100">{card.value}</p>
            {card.hint && <p className="text-[11px] text-mist-500">{card.hint}</p>}
          </motion.button>
        ))}
      </div>

      {/* Metric over time */}
      {chartData.length > 1 && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.5 }}
          className="panel p-5"
        >
          <div className="mb-4 flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-accent-300" />
            <p className="text-sm font-medium text-mist-200">
              {metric} across disclosed periods
            </p>
          </div>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="period"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "#1e293b" }}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  width={44}
                />
                <Tooltip
                  contentStyle={{
                    background: "#0f172a",
                    border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: "#e6edf7" }}
                  itemStyle={{ color: "#7dd3fc" }}
                />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  dot={{ r: 2.5, fill: "#0ea5e9", strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </motion.div>
      )}
    </section>
  );
}
