// Small, dependency-free helpers shared across components.

import type { EvidenceStrengthRating } from "./types";

/** The three canonical evidence-strength ratings + a safe fallback. */
export function normalizeStrength(raw: string | undefined | null): EvidenceStrengthRating {
  const s = (raw || "").trim().toLowerCase();
  if (s.startsWith("strong")) return "Strong";
  if (s.startsWith("moderate")) return "Moderate";
  if (s.startsWith("weak")) return "Weak";
  return "Moderate";
}

/** Tailwind text/border/bg classes per rating (calm, projector-safe). */
export function strengthClasses(rating: EvidenceStrengthRating): {
  text: string;
  border: string;
  bg: string;
  dot: string;
} {
  switch (rating) {
    case "Strong":
      return {
        text: "text-strength-strong",
        border: "border-strength-strong/40",
        bg: "bg-strength-strong/10",
        dot: "bg-strength-strong",
      };
    case "Weak":
      return {
        text: "text-strength-weak",
        border: "border-strength-weak/40",
        bg: "bg-strength-weak/10",
        dot: "bg-strength-weak",
      };
    default:
      return {
        text: "text-strength-moderate",
        border: "border-strength-moderate/40",
        bg: "bg-strength-moderate/10",
        dot: "bg-strength-moderate",
      };
  }
}

/** Best-effort company name from a report (real reports put it under _meta). */
export function companyName(report: { company?: string; _meta?: { company?: string } } | null, fallback: string): string {
  return report?.company || report?._meta?.company || fallback;
}

/** Coerce possibly-null/unknown numbers to a finite number, else 0. */
export function num(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** Shorten a Windows/absolute file path for display (server may run on Windows). */
export function shortLink(link?: string | null): string | null {
  if (!link) return null;
  // file:// or a raw path -> show just the filename + parent folder.
  const cleaned = link.replace(/^file:\/\//, "").replace(/\\/g, "/");
  const parts = cleaned.split("/").filter(Boolean);
  if (parts.length <= 2) return cleaned;
  return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

/** Is this link openable in the browser? Local file paths are not. */
export function isWebLink(link?: string | null): boolean {
  return !!link && /^https?:\/\//i.test(link);
}

/** Mirror of scd.config.slugify: lowercase, non-alphanumerics -> "_". */
export function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}
