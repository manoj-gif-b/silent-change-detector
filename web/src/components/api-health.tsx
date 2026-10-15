"use client";

// Tiny corner indicator for backend liveness + whether a Gemini key is
// configured server-side. The key itself is NEVER returned to the browser --
// only a boolean. Clicking it re-checks. If the API is down, every page shows
// a clear "start the API" hint, so this just confirms connectivity quietly.

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, PlugZap, WifiOff } from "lucide-react";

import { API_URL } from "@/lib/api";

interface Health {
  status: string;
  api_key_present: boolean;
  companies: number;
}

export default function ApiHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);

  const check = async () => {
    try {
      const resp = await fetch(`${API_URL}/health`, { cache: "no-store" });
      const data = (await resp.json()) as Health;
      setHealth(data);
      setDown(false);
    } catch {
      setHealth(null);
      setDown(true);
    }
  };

  useEffect(() => {
    void check();
  }, []);

  return (
    <button
      onClick={() => void check()}
      title={
        down
          ? `Cannot reach API at ${API_URL}. Start it with: uvicorn api:app --reload`
          : `API online${health && !health.api_key_present ? " (no Gemini key set — offline/demo only)" : ""}`
      }
      className="fixed bottom-4 right-4 z-30 flex items-center gap-2 rounded-full border border-white/10 bg-ink-900/80 px-3 py-1.5 text-xs text-mist-400 backdrop-blur-md transition-colors hover:border-accent-500/40 hover:text-mist-100"
    >
      {down ? (
        <WifiOff className="h-3.5 w-3.5 text-strength-weak" />
      ) : health && !health.api_key_present ? (
        <PlugZap className="h-3.5 w-3.5 text-strength-moderate" />
      ) : (
        <Activity className="h-3.5 w-3.5 text-strength-strong" />
      )}
      <AnimatePresence mode="wait">
        <motion.span
          key={down ? "down" : health?.status ?? "checking"}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          {down ? "API offline" : health ? `${health.companies} report${health.companies === 1 ? "" : "s"}` : "checking…"}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
