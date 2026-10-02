"use client";

import { useMemo, useState } from "react";
import { normLufs, type BridgeTrack, type TrackDetail } from "@/lib/api";
import { useWidth } from "./useWidth";

type Point = { t: number; energy: number; valence: number | null; song: number; local: number };

const H = 230;
const M = { top: 28, right: 64, bottom: 30, left: 36 };

function smooth(xs: number[], k = 2) {
  return xs.map((_, i) => {
    const w = xs.slice(Math.max(0, i - k), i + k + 1);
    return w.reduce((a, b) => a + b, 0) / w.length;
  });
}

const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Energy and valence *inside* every song, laid end to end across the whole album. */
export function TrajectoryChart({ tracks, details }: { tracks: BridgeTrack[]; details: Record<string, TrackDetail> }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Point | null>(null);

  const { points, bounds, total } = useMemo(() => {
    const points: Point[] = [];
    const bounds: { start: number; end: number }[] = [];
    let offset = 0;
    tracks.forEach((tr, song) => {
      const d = details[tr.id];
      if (!d) return;
      const hop = d.trajectory.hop_s;
      const energy = smooth(d.trajectory.energy.map(normLufs));
      // Older/coarser analyses carry only a song-level valence: draw it flat across the song.
      const val = d.trajectory.valence.length
        ? smooth(d.trajectory.valence)
        : d.global.valence != null
          ? energy.map(() => d.global.valence as number)
          : null;
      energy.forEach((e, i) =>
        points.push({ t: offset + i * hop, energy: e, valence: val ? val[i] ?? null : null, song, local: i * hop }),
      );
      bounds.push({ start: offset, end: offset + d.duration_s });
      offset += d.duration_s;
    });
    return { points, bounds, total: offset };
  }, [tracks, details]);

  const iw = width - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const x = (t: number) => M.left + (t / Math.max(total, 1)) * iw;
  const y = (v: number) => M.top + (1 - v) * ih;

  const path = (key: "energy" | "valence") => {
    let d = "";
    let prevSong = -1;
    for (const p of points) {
      const v = p[key];
      if (v == null) {
        prevSong = -1;
        continue;
      }
      d += `${p.song !== prevSong ? "M" : "L"}${x(p.t).toFixed(1)},${y(v).toFixed(1)}`;
      prevSong = p.song;
    }
    return d;
  };

  const last = points[points.length - 1];
  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const t = ((e.clientX - rect.left) / rect.width) * total;
    let best = points[0];
    for (const p of points) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    setHover(best ?? null);
  };

  if (!points.length) return <div className="h-[230px] animate-pulse rounded-lg bg-grid/40" />;

  return (
    <div ref={ref} className="relative">
      <div className="mb-2 flex gap-4 text-xs text-ink-2">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: "var(--s1)" }} /> Energy
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: "var(--s2)" }} /> Valence (mood)
        </span>
        <span className="text-muted">within each song, end to end</span>
      </div>
      <svg width={width} height={H} role="img" aria-label="Energy and valence across the bridge">
        {bounds.map((b, i) => (
          <g key={i}>
            {i % 2 === 1 && (
              <rect x={x(b.start)} y={M.top} width={x(b.end) - x(b.start)} height={ih} fill="var(--grid)" opacity={0.35} />
            )}
            <text
              x={(x(b.start) + x(b.end)) / 2}
              y={M.top - 10}
              textAnchor="middle"
              fontSize={10}
              fill={tracks[i].role === "waypoint" ? "var(--ink)" : "var(--muted)"}
              fontWeight={tracks[i].role === "waypoint" ? 600 : 400}
            >
              {i + 1}
            </text>
          </g>
        ))}
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line x1={M.left} x2={M.left + iw} y1={y(v)} y2={y(v)} stroke={v === 0 ? "var(--axis)" : "var(--grid)"} />
            <text x={M.left - 6} y={y(v) + 3} textAnchor="end" fontSize={10} fill="var(--muted)" className="tabular">
              {v}
            </text>
          </g>
        ))}
        {Array.from({ length: Math.floor(total / 300) + 1 }, (_, i) => i * 300).map((t) => (
          <text key={t} x={x(t)} y={H - 10} textAnchor="middle" fontSize={10} fill="var(--muted)" className="tabular">
            {Math.round(t / 60)}m
          </text>
        ))}
        <path d={path("valence")} fill="none" stroke="var(--s2)" strokeWidth={2} strokeLinejoin="round" />
        <path d={path("energy")} fill="none" stroke="var(--s1)" strokeWidth={2} strokeLinejoin="round" />
        {last && (
          <>
            <text x={x(last.t) + 6} y={y(last.energy) + 3} fontSize={10} fill="var(--ink-2)">
              Energy
            </text>
            {last.valence != null && (
              <text x={x(last.t) + 6} y={y(last.valence) + 3} fontSize={10} fill="var(--ink-2)">
                Valence
              </text>
            )}
          </>
        )}
        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.t)} x2={x(hover.t)} y1={M.top} y2={M.top + ih} stroke="var(--axis)" />
            <circle cx={x(hover.t)} cy={y(hover.energy)} r={4} fill="var(--s1)" stroke="var(--surface)" strokeWidth={2} />
            {hover.valence != null && (
              <circle cx={x(hover.t)} cy={y(hover.valence)} r={4} fill="var(--s2)" stroke="var(--surface)" strokeWidth={2} />
            )}
          </g>
        )}
        <rect
          x={M.left}
          y={M.top}
          width={iw}
          height={ih}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs shadow-sm"
          style={{ left: Math.min(x(hover.t) + 12, width - 190), top: 36 }}
        >
          <div className="font-medium text-ink">
            {hover.song + 1}. {tracks[hover.song].title}
          </div>
          <div className="text-muted">{tracks[hover.song].artist} · {fmtTime(hover.local)}</div>
          <div className="tabular mt-1 text-ink-2">
            Energy {hover.energy.toFixed(2)}
            {hover.valence != null && <> · Valence {hover.valence.toFixed(2)}</>}
          </div>
        </div>
      )}
    </div>
  );
}
