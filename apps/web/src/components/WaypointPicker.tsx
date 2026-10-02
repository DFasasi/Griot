"use client";

import { useEffect, useState } from "react";
import { api, type TrackHit } from "@/lib/api";

export function WaypointPicker({ onPick, placeholder }: { onPick: (t: TrackHit) => void; placeholder: string }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<TrackHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (!q.trim()) return;
    const h = setTimeout(() => {
      api.search(q).then(setResults).catch(() => setResults([]));
    }, 150);
    return () => clearTimeout(h);
  }, [q]);

  const hits = q.trim() ? results : [];

  const pick = (t: TrackHit) => {
    onPick(t);
    setQ("");
    setResults([]);
    setOpen(false);
  };

  return (
    <div className="relative">
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, hits.length - 1));
          if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          if (e.key === "Enter" && hits[active]) pick(hits[active]);
        }}
        placeholder={placeholder}
        className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-accent"
      />
      {open && hits.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-line bg-surface py-1 shadow-lg">
          {hits.map((h, i) => (
            <li key={h.id}>
              <button
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(h)}
                onMouseEnter={() => setActive(i)}
                className={`flex w-full items-baseline justify-between gap-3 px-3 py-1.5 text-left text-sm ${i === active ? "bg-grid/50" : ""}`}
              >
                <span className="truncate">
                  <span className="text-ink">{h.title}</span> <span className="text-muted">— {h.artist}</span>
                </span>
                <span className="tabular shrink-0 text-xs text-muted">
                  {Math.round(h.bpm)} · {h.camelot}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
