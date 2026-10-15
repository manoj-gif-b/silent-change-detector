import type { Config } from "tailwindcss";

/**
 * Silent Change Detector -- dark, calm, professional palette.
 * Colours are deep slate/navy with a single cool accent so the 3D graph,
 * charts and badges stay legible on both a laptop and a projector.
 */
const config: Config = {
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Base surfaces (near-black, low-glare for projectors).
        ink: {
          950: "#070b14",
          900: "#0b1120",
          850: "#0f172a",
          800: "#131c31",
          700: "#1e293b",
        },
        // Muted text + borders.
        mist: {
          100: "#e6edf7",
          300: "#b8c4d9",
          400: "#8fa0bd",
          500: "#64748b",
          600: "#475569",
        },
        // Cool accent (links, focus, active node).
        accent: {
          300: "#7dd3fc",
          400: "#38bdf8",
          500: "#0ea5e9",
          600: "#0284c7",
        },
        // Evidence-strength semantics.
        strength: {
          strong: "#34d399",
          moderate: "#fbbf24",
          weak: "#fb7185",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 20px 40px -24px rgba(0,0,0,0.8)",
        glow: "0 0 0 1px rgba(56,189,248,0.35), 0 0 24px -6px rgba(56,189,248,0.45)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulse: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.6s ease-out both",
        pulse: "pulse 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
