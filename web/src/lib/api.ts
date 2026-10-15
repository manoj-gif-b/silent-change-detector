// Thin fetch client for the FastAPI backend (api.py, default http://127.0.0.1:8000).
// The Gemini API key is NEVER sent to or stored by the browser -- all LLM work
// happens server-side. This client only moves reports and run status.

import type { CompaniesResponse, Report, RunAccepted, RunRequest, RunStatus } from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://127.0.0.1:8000";

export const API_URL = API_BASE;

async function asJson<T>(resp: Response): Promise<T> {
  const text = await resp.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text?.slice(0, 500) };
  }
  if (!resp.ok) {
    const detail =
      (body as { detail?: string; error?: string })?.detail ??
      (body as { error?: string })?.error ??
      `Request failed (HTTP ${resp.status})`;
    throw new Error(detail);
  }
  return body as T;
}

export async function fetchCompanies(): Promise<CompaniesResponse> {
  const resp = await fetch(`${API_BASE}/companies`, { cache: "no-store" });
  return asJson<CompaniesResponse>(resp);
}

export async function fetchReport(slug: string): Promise<Report> {
  const resp = await fetch(`${API_BASE}/report/${encodeURIComponent(slug)}`, {
    cache: "no-store",
  });
  return asJson<Report>(resp);
}

export async function fetchStatus(slug: string): Promise<RunStatus> {
  const resp = await fetch(`${API_BASE}/status/${encodeURIComponent(slug)}`, {
    cache: "no-store",
  });
  return asJson<RunStatus>(resp);
}

export async function startRun(req: RunRequest): Promise<RunAccepted> {
  const resp = await fetch(`${API_BASE}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  // The API may answer 202 (accepted) or 400 (bad input) with a JSON body; parse either.
  return asJson<RunAccepted>(resp);
}

/** URL for the server-rendered PDF export (opens in a new tab / triggers download). */
export function reportPdfUrl(slug: string): string {
  return `${API_BASE}/report/${encodeURIComponent(slug)}/pdf`;
}

