"use client";

// Section 2 -- 3D evidence graph: the discovery sits at the centre with each
// independent source as a node around it. Slow auto-rotation (OrbitControls),
// hover highlights, click opens the SourcePanel with document, page, date/period
// and the exact quote. Geometry is deliberately tiny: low-poly octahedra, ONE
// batched line mesh for all connectors, no post-processing, dpr capped -- so it
// runs smoothly on a normal laptop or projector.

import { useEffect, useMemo, useState } from "react";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import { motion } from "framer-motion";
import { Box, List } from "lucide-react";

import { fromEvidenceItem, useEvidence } from "./evidence-context";
import type { EvidenceItem, Report } from "@/lib/types";

const MAX_NODES = 12; // hard cap so geometry stays light

/** Collect unique sources: evidence[] first, de-duplicated with evidence_trail. */
function collectSources(report: Report): EvidenceItem[] {
  const seen = new Set<string>();
  const out: EvidenceItem[] = [];
  const push = (
    doc: string,
    page: number | null,
    quote: string,
    period?: string | null,
    link?: string | null,
  ) => {
    const key = `${doc}#${page ?? ""}#${quote.slice(0, 60)}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ source_doc: doc, page, period: period ?? null, quote, link: link ?? null });
  };
  for (const e of report.evidence ?? []) push(e.source_doc, e.page, e.quote, e.period, e.link);
  for (const t of report.evidence_trail ?? []) push(t.document, t.page, t.quote, t.period, t.link);
  return out.slice(0, MAX_NODES);
}

/** Ring position for node i of n (slight vertical wobble for depth). */
function nodePosition(i: number, n: number): [number, number, number] {
  const angle = (i / Math.max(n, 1)) * Math.PI * 2 - Math.PI / 2;
  const r = 3.4;
  return [Math.cos(angle) * r, Math.sin(i * 1.7) * 0.7, Math.sin(angle) * r];
}

interface SceneProps {
  items: EvidenceItem[];
  hovered: number | null;
  setHovered: (i: number | null) => void;
  onSelect: (item: EvidenceItem) => void;
}

/** Everything inside <Canvas>. Pure scene; no layout state beyond hover. */
function Scene({ items, hovered, setHovered, onSelect }: SceneProps) {
  // One batched LineSegments: 2 vertices (centre -> node) per source.
  const linePositions = useMemo(() => {
    const arr = new Float32Array(items.length * 6);
    items.forEach((_, i) => {
      const [x, y, z] = nodePosition(i, items.length);
      arr.set([0, 0, 0, x, y, z], i * 6);
    });
    return arr;
  }, [items]);

  return (
    <>
      <ambientLight intensity={0.7} />
      <pointLight position={[6, 6, 8]} intensity={0.9} />

      {/* Connector spokes (single draw call) */}
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[linePositions, 3]} />
        </bufferGeometry>
        <lineBasicMaterial color="#38bdf8" transparent opacity={0.25} />
      </lineSegments>

      {/* Centre: the discovery */}
      <mesh>
        <sphereGeometry args={[0.85, 20, 14]} />
        <meshStandardMaterial color="#0ea5e9" emissive="#0369a1" emissiveIntensity={0.6} />
      </mesh>
      <Html position={[0, -1.45, 0]} center style={{ pointerEvents: "none" }}>
        <span className="whitespace-nowrap rounded-full border border-white/10 bg-ink-900/85 px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-mist-300">
          Discovery
        </span>
      </Html>

      {/* Source nodes (low-poly octahedra: 8 triangles each) */}
      {items.map((item, i) => {
        const pos = nodePosition(i, items.length);
        const active = hovered === i;
        return (
          <mesh
            key={`${item.source_doc}-${i}`}
            position={pos}
            scale={active ? 1.5 : 1}
            onClick={(e: ThreeEvent<MouseEvent>) => {
              e.stopPropagation();
              onSelect(item);
            }}
            onPointerOver={(e: ThreeEvent<PointerEvent>) => {
              e.stopPropagation();
              setHovered(i);
              document.body.style.cursor = "pointer";
            }}
            onPointerOut={() => {
              setHovered(null);
              document.body.style.cursor = "auto";
            }}
          >
            <octahedronGeometry args={[0.2, 0]} />
            <meshStandardMaterial
              color={active ? "#7dd3fc" : "#0284c7"}
              emissive={active ? "#38bdf8" : "#075985"}
              emissiveIntensity={active ? 1.0 : 0.4}
            />
          </mesh>
        );
      })}

      {/* Hover label for the active node */}
      {hovered != null && items[hovered] && (
        <Html position={nodePosition(hovered, items.length)} center style={{ pointerEvents: "none" }}>
          <span className="max-w-[220px] truncate rounded-md border border-white/10 bg-ink-900/95 px-2 py-1 text-[11px] text-mist-200 shadow-panel">
            {items[hovered].source_doc}
            {items[hovered].period ? ` · ${items[hovered].period}` : ""}
          </span>
        </Html>
      )}

      {/* Orbit controls: slow auto-rotation, user can drag/zoom (fallback). */}
      <OrbitControls
        makeDefault
        enablePan={false}
        enableDamping
        dampingFactor={0.05}
        autoRotate
        autoRotateSpeed={hovered != null ? 0 : 0.7}
        minDistance={4}
        maxDistance={14}
        minPolarAngle={Math.PI / 5}
        maxPolarAngle={(Math.PI * 4) / 5}
      />
    </>
  );
}

/**
 * Section 2 wrapper. Renders the 3D graph by default, with a toggle to a 2D
 * list view for accessibility and low-power laptops. The <Canvas> is only
 * mounted on the client (never during SSR) to avoid hydration/WebGL issues.
 */
export default function EvidenceGraph({ report }: { report: Report }) {
  const items = useMemo(() => collectSources(report), [report]);
  const { selectEvidence } = useEvidence();
  const [view, setView] = useState<"3d" | "list">("3d");
  const [hovered, setHovered] = useState<number | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  // If there are no nodes, fall back to the list view automatically.
  useEffect(() => {
    if (items.length === 0) setView("list");
  }, [items.length]);

  const select = (item: EvidenceItem) => selectEvidence(fromEvidenceItem(item));

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <p className="section-title">Evidence map</p>
        <span className="text-[11px] text-mist-500">
          {items.length} independent source{items.length === 1 ? "" : "s"} · click a node for the
          exact quote
        </span>

        <div className="ml-auto flex rounded-lg border border-white/10 bg-ink-850/70 p-0.5">
          <button
            onClick={() => setView("3d")}
            disabled={items.length === 0}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40 ${
              view === "3d" ? "bg-accent-500/15 text-accent-200" : "text-mist-400 hover:text-mist-200"
            }`}
            aria-pressed={view === "3d"}
          >
            <Box className="h-3.5 w-3.5" /> 3D
          </button>
          <button
            onClick={() => setView("list")}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              view === "list" ? "bg-accent-500/15 text-accent-200" : "text-mist-400 hover:text-mist-200"
            }`}
            aria-pressed={view === "list"}
          >
            <List className="h-3.5 w-3.5" /> 2D list
          </button>
        </div>
      </div>

      {view === "3d" ? (
        <div className="panel relative h-[420px] overflow-hidden sm:h-[480px]">
          {mounted ? (
            <Canvas
              dpr={[1, 1.75]}
              camera={{ position: [0, 1.5, 8], fov: 45 }}
              gl={{ antialias: true, powerPreference: "high-performance" }}
              onPointerMissed={() => setHovered(null)}
            >
              <color attach="background" args={["#0b1120"]} />
              <Scene items={items} hovered={hovered} setHovered={setHovered} onSelect={select} />
            </Canvas>
          ) : (
            <div className="grid h-full place-items-center text-sm text-mist-500">
              Loading 3D view…
            </div>
          )}

          <p className="pointer-events-none absolute bottom-3 left-4 text-[11px] text-mist-600">
            drag to rotate · scroll to zoom · click a node to open its source
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map((item, i) => (
            <motion.button
              key={`${item.source_doc}-${i}`}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: i * 0.04 }}
              onClick={() => select(item)}
              className="panel panel-hover p-4 text-left"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-sm text-accent-300">{item.source_doc}</span>
                <span className="shrink-0 text-[11px] text-mist-500">
                  p.{item.page ?? "—"}
                  {item.period ? ` · ${item.period}` : ""}
                </span>
              </div>
              <blockquote className="quote mt-2 line-clamp-3">{item.quote}</blockquote>
            </motion.button>
          ))}
          {items.length === 0 && (
            <p className="panel p-4 text-sm text-mist-500 sm:col-span-2">
              No source evidence recorded in this report.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

