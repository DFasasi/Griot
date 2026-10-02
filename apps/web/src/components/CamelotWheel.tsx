"use client";

import { useState } from "react";
import type { BridgeTrack } from "@/lib/api";

const S = 240;
const C = S / 2;
const R_OUT = 100; // major (B)
const R_IN = 68; // minor (A)

function pos(code: string) {
  const n = parseInt(code, 10);
  const minor = code.toUpperCase().endsWith("A");
  const a = ((n - 1) / 12) * 2 * Math.PI - Math.PI / 2;
  const r = minor ? R_IN : R_OUT;
  return { x: C + r * Math.cos(a), y: C + r * Math.sin(a) };
}

/** The harmonic route: each track's key on the Camelot wheel, joined in play order. */
export function CamelotWheel({ tracks }: { tracks: BridgeTrack[] }) {
  const [hover, setHover] = useState<number | null>(null);
  // Consecutive tracks in the same key collapse into one stop with a count.
  const stops: { code: string; first: number; count: number }[] = [];
  tracks.forEach((t, i) => {
    const last = stops[stops.length - 1];
    if (last && last.code === t.camelot) last.count++;
    else stops.push({ code: t.camelot, first: i, count: 1 });
  });
  const placed = stops.map((s) => pos(s.code));

  return (
    <div className="relative">
      <svg width={S} height={S} viewBox={`0 0 ${S} ${S}`} role="img" aria-label="Key path on the Camelot wheel">
        <circle cx={C} cy={C} r={R_OUT} fill="none" stroke="var(--grid)" />
        <circle cx={C} cy={C} r={R_IN} fill="none" stroke="var(--grid)" />
        {Array.from({ length: 12 }, (_, i) => {
          const o = pos(`${i + 1}B`);
          const a = ((i / 12) * 2 * Math.PI) - Math.PI / 2;
          return (
            <g key={i}>
              <text x={C + 116 * Math.cos(a)} y={C + 116 * Math.sin(a) + 3} textAnchor="middle" fontSize={9} fill="var(--muted)">
                {i + 1}
              </text>
              <circle cx={o.x} cy={o.y} r={1.5} fill="var(--axis)" />
              <circle cx={pos(`${i + 1}A`).x} cy={pos(`${i + 1}A`).y} r={1.5} fill="var(--axis)" />
            </g>
          );
        })}
        <text x={C} y={C - 4} textAnchor="middle" fontSize={9} fill="var(--muted)">outer = major</text>
        <text x={C} y={C + 8} textAnchor="middle" fontSize={9} fill="var(--muted)">inner = minor</text>
        <polyline
          points={placed.map((p) => `${p.x},${p.y}`).join(" ")}
          fill="none"
          stroke="var(--s1)"
          strokeWidth={2}
          strokeLinejoin="round"
          opacity={0.7}
        />
        {placed.map((p, i) => {
          const stop = stops[i];
          const t = tracks[stop.first];
          const isWp = tracks.slice(stop.first, stop.first + stop.count).some((x) => x.role === "waypoint");
          return (
            <g key={i} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <circle cx={p.x} cy={p.y} r={12} fill="transparent" />
              <circle cx={p.x} cy={p.y} r={isWp ? 6 : 4.5} fill={isWp ? "var(--ink)" : "var(--s1)"} stroke="var(--surface)" strokeWidth={2} />
              {stop.count > 1 && (
                <text x={p.x + 9} y={p.y - 7} fontSize={9} fill="var(--ink-2)" className="tabular">
                  ×{stop.count}
                </text>
              )}
              <title>{`${t.camelot}`}</title>
            </g>
          );
        })}
      </svg>
      {hover != null && (
        <div className="pointer-events-none absolute left-2 top-2 max-w-56 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs shadow-sm">
          <div className="font-medium text-ink">{stops[hover].code}</div>
          {tracks.slice(stops[hover].first, stops[hover].first + stops[hover].count).map((t, j) => (
            <div key={j} className="truncate text-muted">
              {stops[hover].first + j + 1}. {t.title} · {Math.round(t.bpm)} BPM
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
