"use client";

import { useState } from "react";
import { TERMS, TERM_LABEL, type BridgeTrack, type Transition } from "@/lib/api";
import { useWidth } from "./useWidth";

const ROW = 22;
const GAP = 8;
const LABEL_W = 46;
const VALUE_W = 44;

/** Stacked cost of every seam, split by term: what made each transition easy or hard. */
export function TransitionBars({ tracks, transitions }: { tracks: BridgeTrack[]; transitions: Transition[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ row: number; term: string; x: number; y: number } | null>(null);
  const [asTable, setAsTable] = useState(false);
  const max = Math.max(...transitions.map((t) => t.cost), 0.01);
  const iw = width - LABEL_W - VALUE_W;
  const H = transitions.length * (ROW + GAP);

  return (
    <div ref={ref} className="relative">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
        {TERMS.map((k, i) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded-sm" style={{ background: `var(--s${i + 1})` }} />
            {TERM_LABEL[k]}
          </span>
        ))}
        <button onClick={() => setAsTable(!asTable)} className="ml-auto text-accent hover:underline">
          {asTable ? "Chart view" : "Table view"}
        </button>
      </div>

      {asTable ? (
        <div className="overflow-x-auto">
          <table className="tabular w-full text-xs">
            <thead className="text-muted">
              <tr>
                <th className="py-1 text-left font-normal">Seam</th>
                {TERMS.map((k) => (
                  <th key={k} className="py-1 text-right font-normal">{k}</th>
                ))}
                <th className="py-1 text-right font-normal">total</th>
              </tr>
            </thead>
            <tbody>
              {transitions.map((t, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-1">{i + 1}→{i + 2}</td>
                  {TERMS.map((k) => (
                    <td key={k} className="py-1 text-right text-ink-2">{(t.terms[k] ?? 0).toFixed(2)}</td>
                  ))}
                  <td className="py-1 text-right font-medium">{t.cost.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <svg width={width} height={H} role="img" aria-label="Transition cost by term">
          {transitions.map((t, row) => {
            const y = row * (ROW + GAP);
            const segs = TERMS.map((k, i) => ({ k, i, v: t.terms[k] ?? 0 })).filter((s) => s.v > 0.001);
            let cx = LABEL_W;
            return (
              <g key={row}>
                <text x={LABEL_W - 8} y={y + ROW / 2 + 4} textAnchor="end" fontSize={10} fill="var(--muted)" className="tabular">
                  {row + 1}→{row + 2}
                </text>
                {segs.map((s, j) => {
                  const w = (s.v / max) * iw;
                  const x0 = cx;
                  cx += w;
                  const isLast = j === segs.length - 1;
                  const visW = Math.max(0, w - (isLast ? 0 : 2)); // 2px surface gap between segments
                  const r = isLast ? Math.min(4, visW / 2) : 0; // round only the data end
                  const d = `M${x0},${y} h${visW - r} q${r},0 ${r},${r} v${ROW - 2 * r} q0,${r} ${-r},${r} h${-(visW - r)} z`;
                  return (
                    <path
                      key={s.k}
                      d={d}
                      fill={`var(--s${s.i + 1})`}
                      opacity={hover && (hover.row !== row || hover.term !== s.k) ? 0.45 : 1}
                      onPointerEnter={() => setHover({ row, term: s.k, x: x0 + w / 2, y })}
                      onPointerLeave={() => setHover(null)}
                    />
                  );
                })}
                <text x={cx + 6} y={y + ROW / 2 + 4} fontSize={10} fill="var(--ink-2)" className="tabular">
                  {t.cost.toFixed(2)}
                </text>
              </g>
            );
          })}
        </svg>
      )}

      {hover && !asTable && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs shadow-sm"
          style={{ left: Math.min(hover.x, width - 220), top: hover.y + ROW + 34 }}
        >
          <div className="font-medium text-ink">{TERM_LABEL[hover.term]}</div>
          <div className="text-muted">
            {tracks[hover.row].title} → {tracks[hover.row + 1].title}
          </div>
          <div className="tabular text-ink-2">
            {(transitions[hover.row].terms[hover.term] ?? 0).toFixed(3)} of {transitions[hover.row].cost.toFixed(2)}
          </div>
        </div>
      )}
    </div>
  );
}
