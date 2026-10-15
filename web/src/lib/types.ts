// TypeScript mirror of the backend report.json schema (see report.py).
// Kept deliberately faithful: the frontend never invents fields.

export type EvidenceStrengthRating = "Strong" | "Moderate" | "Weak";

/** One evidence item: a document, page, period, verbatim quote and source link. */
export interface EvidenceItem {
  source_doc: string;
  page: number | null;
  period?: string | null;
  quote: string;
  link?: string | null;
}

/** An evidence-trail row ties a claim (finding) to its source. */
export interface EvidenceTrailItem {
  finding: string;
  document: string;
  page: number | null;
  quote: string;
  period?: string | null;
  link?: string | null;
}

/** A point on the timeline: how a metric changed over time. */
export interface TimelinePoint {
  period: string;
  date?: string | null;
  value: number;
  metric: string;
}

/** The headline numbers that support the finding (shape varies by detector). */
export interface ReportNumbers {
  kpi?: string;
  periods_reported?: number;
  last_reported_period?: string | null;
  first_missing_period?: string | null;
  trailing_missing_periods?: number;
  redefined?: boolean;
  // narrative / peer detectors add free-form extras; allow them without losing safety.
  [key: string]: unknown;
}

/** A red-team check (sector-wide, accounting, one-off, seasonality). */
export interface RedTeamCheck {
  name: string;
  description?: string;
  supported: boolean;
  reasoning: string;
}

/** The evidence_strength block. */
export interface EvidenceStrength {
  rating: EvidenceStrengthRating;
  reasoning: string;
  downgrade_note?: string | null;
  checks_survived?: string | null;
}

/** A ranked alternative finding considered but not chosen. */
export interface Candidate {
  rank: number;
  detector: string;
  title: string;
  adjusted_score: number;
}

export interface ReportMeta {
  company: string;
  detector?: string;
  anomaly_score?: number;
  redteam_checks?: RedTeamCheck[];
  other_candidates?: Candidate[];
  no_investment_advice?: boolean;
  [key: string]: unknown;
}

/** The full report.json document. */
export interface Report {
  discovery: string;
  why_it_matters: string[];
  evidence: EvidenceItem[];
  timeline: TimelinePoint[];
  numbers: ReportNumbers;
  counterargument: string;
  falsification_condition: string;
  evidence_strength: EvidenceStrength;
  evidence_trail: EvidenceTrailItem[];
  // Present in real reports; kept optional so a bare report still type-checks.
  company?: string;
  _meta?: ReportMeta;
}

// ---- API shapes -----------------------------------------------------------

export interface CompanySummary {
  slug: string;
  company: string;
  evidence_strength: string;
}

export interface CompaniesResponse {
  companies: CompanySummary[];
  count: number;
}

export type RunState = "idle" | "running" | "done" | "error";

export interface RunStatus {
  company: string;
  slug: string;
  state: RunState;
  report_exists: boolean;
  logs: string[];
  started_at: number | null;
  finished_at: number | null;
  elapsed_seconds: number | null;
  error: string | null;
}

export interface RunRequest {
  company: string;
  demo?: boolean;
  max_chunks?: number | null;
}

export interface RunAccepted {
  slug: string;
  state: RunState;
  message?: string;
  company?: string;
  error?: string;
}
