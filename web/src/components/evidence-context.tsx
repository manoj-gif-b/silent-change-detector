"use client";

// Shared "selected evidence" store. Any clickable claim (hero, timeline, chart,
// evidence list) calls selectEvidence() and the SourcePanel slides in with the
// document, page, period and exact quote -- so every claim traces to its source.

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { EvidenceItem, EvidenceTrailItem } from "../lib/types";

export interface SelectedEvidence {
  /** Where this evidence came from (drives the panel heading). */
  kind: "evidence" | "trail";
  doc: string;
  page: number | null;
  quote: string;
  period?: string | null;
  link?: string | null;
  /** Optional label for what the claim is (e.g. a metric name). */
  label?: string;
}

interface EvidenceContextValue {
  selected: SelectedEvidence | null;
  selectEvidence: (e: SelectedEvidence) => void;
  clear: () => void;
}

const EvidenceContext = createContext<EvidenceContextValue | null>(null);

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<SelectedEvidence | null>(null);

  const value = useMemo<EvidenceContextValue>(
    () => ({
      selected,
      selectEvidence: (e) => setSelected(e),
      clear: () => setSelected(null),
    }),
    [selected],
  );

  return <EvidenceContext.Provider value={value}>{children}</EvidenceContext.Provider>;
}

export function useEvidence(): EvidenceContextValue {
  const ctx = useContext(EvidenceContext);
  if (!ctx) throw new Error("useEvidence must be used within <EvidenceProvider>");
  return ctx;
}

/** Helper: adapt an evidence[] item into a SelectedEvidence payload. */
export function fromEvidenceItem(item: EvidenceItem, label?: string): SelectedEvidence {
  return {
    kind: "evidence",
    doc: item.source_doc,
    page: item.page,
    quote: item.quote,
    period: item.period,
    link: item.link,
    label,
  };
}

/** Helper: adapt an evidence_trail[] item into a SelectedEvidence payload. */
export function fromTrailItem(item: EvidenceTrailItem): SelectedEvidence {
  return {
    kind: "trail",
    doc: item.document,
    page: item.page,
    quote: item.quote,
    period: item.period,
    link: item.link,
    label: item.finding,
  };
}
