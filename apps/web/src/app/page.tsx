"use client";

import { useEffect, useRef, useState } from "react";
import { CamelotWheel } from "@/components/CamelotWheel";
import { TrajectoryChart } from "@/components/TrajectoryChart";
import { TransitionBars } from "@/components/TransitionBars";
import { WaypointPicker } from "@/components/WaypointPicker";
import { api, type ArcPoint, type Bridge, type TrackDetail, type TrackHit } from "@/lib/api";
import { takeQueuedWaypoints } from "@/lib/library";

const ARCS: Record<string, { label: string; arc: ArcPoint[] | null }> = {
  none: { label: "Follow the songs", arc: null },
  rise: { label: "Build up", arc: [{ t: 0, energy: 0.3 }, { t: 1, energy: 0.9 }] },
  peak: { label: "Peak in the middle", arc: [{ t: 0, energy: 0.35 }, { t: 0.5, energy: 0.95 }, { t: 1, energy: 0.35 }] },
  down: { label: "Wind down", arc: [{ t: 0, energy: 0.85 }, { t: 1, energy: 0.25 }] },
  valley: { label: "Dip and return", arc: [{ t: 0, energy: 0.8 }, { t: 0.5, energy: 0.3 }, { t: 1, energy: 0.8 }] },
};

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {note && <span className="text-xs text-muted">{note}</span>}
      </div>
      {children}
    </section>
  );
}

export default function Studio() {
  const [waypoints, setWaypoints] = useState<TrackHit[]>([]);
  const [length, setLength] = useState(10);
  const [arc, setArc] = useState("none");
  const [prompt, setPrompt] = useState("");
  const [deepCuts, setDeepCuts] = useState(false);
  const [maxPerArtist, setMaxPerArtist] = useState(1);
  const [bridge, setBridge] = useState<Bridge | null>(null);
  const [details, setDetails] = useState<Record<string, TrackDetail>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ratings, setRatings] = useState<Record<number, 1 | -1>>({});
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => () => audio.current?.pause(), []);

  // Songs sent over from the Library page arrive as queued waypoints.
  useEffect(() => {
    const queued = takeQueuedWaypoints();
    if (queued.length)
      // localStorage exists only after mount; reading it in render would break hydration.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setWaypoints((w) => [
        ...w,
        ...queued
          .filter((q) => !w.some((x) => x.id === q.id))
          .map((q) => ({ ...q, bpm: 0, camelot: "", year: null, tier: "", tags: [] })),
      ]);
  }, []);

  const move = (i: number, d: number) =>
    setWaypoints((w) => {
      const j = i + d;
      if (j < 0 || j >= w.length) return w;
      const c = [...w];
      [c[i], c[j]] = [c[j], c[i]];
      return c;
    });

  const build = async () => {
    setBusy(true);
    setError(null);
    setRatings({});
    try {
      const b = await api.bridge({
        waypoints: waypoints.map((w) => w.id),
        length: Math.max(length, waypoints.length - 1),
        arc: ARCS[arc].arc,
        prompt: prompt.trim() || null,
        filters: { min_playcount: deepCuts ? null : 5000, max_per_artist: maxPerArtist },
      });
      setBridge(b);
      const missing = b.tracks.filter((t) => !details[t.id]);
      const got = await Promise.all(missing.map((t) => api.track(t.id).catch(() => null)));
      setDetails((d) => {
        const n = { ...d };
        got.forEach((g) => g && (n[g.id] = g));
        return n;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const play = (id: string, url: string | null) => {
    audio.current?.pause();
    if (!url || playing === id) {
      setPlaying(null);
      return;
    }
    audio.current = new Audio(url);
    audio.current.play().catch(() => setPlaying(null));
    audio.current.onended = () => setPlaying(null);
    setPlaying(id);
  };

  const rate = (pos: number, r: 1 | -1) => {
    if (!bridge) return;
    setRatings((x) => ({ ...x, [pos]: r }));
    api.feedback(bridge.id, pos, r).catch(() => {});
  };

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Build a bridge</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-2">
          Pick the songs you want to pass through. Griot fills the gaps with real tracks so every seam
          lands, matching each song&apos;s ending to the next one&apos;s opening using full-song analysis.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="space-y-5">
          <Panel title="Waypoints" note={`${waypoints.length} songs`}>
            <ol className="mb-3 space-y-1.5">
              {waypoints.map((w, i) => (
                <li key={w.id} className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 text-sm">
                  <span className="tabular w-4 text-xs text-muted">{i + 1}</span>
                  <span className="min-w-0 flex-1 truncate">
                    {w.title} <span className="text-muted">— {w.artist}</span>
                  </span>
                  <button aria-label="Move up" onClick={() => move(i, -1)} className="text-muted hover:text-ink">↑</button>
                  <button aria-label="Move down" onClick={() => move(i, 1)} className="text-muted hover:text-ink">↓</button>
                  <button
                    aria-label="Remove"
                    onClick={() => setWaypoints((x) => x.filter((_, j) => j !== i))}
                    className="text-muted hover:text-ink"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ol>
            <WaypointPicker
              placeholder={waypoints.length ? "Add another waypoint…" : "Search a song to start from…"}
              onPick={(t) => setWaypoints((w) => (w.some((x) => x.id === t.id) ? w : [...w, t]))}
            />
          </Panel>

          <Panel title="Shape">
            <label className="mb-1 flex justify-between text-xs text-ink-2">
              <span>Bridge tracks</span>
              <span className="tabular text-ink">{length}</span>
            </label>
            <input type="range" min={2} max={30} value={length} onChange={(e) => setLength(+e.target.value)} className="mb-4 w-full accent-[var(--accent)]" />

            <label className="mb-1 block text-xs text-ink-2">Energy arc</label>
            <select value={arc} onChange={(e) => setArc(e.target.value)} className="mb-4 w-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm">
              {Object.entries(ARCS).map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>

            <label className="mb-1 block text-xs text-ink-2">Steer with words (optional)</label>
            <input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g. warmer, more acoustic"
              className="mb-4 w-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm placeholder:text-muted"
            />

            <div className="flex items-center justify-between text-sm">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={deepCuts} onChange={(e) => setDeepCuts(e.target.checked)} />
                Deep cuts
              </label>
              <label className="flex items-center gap-2 text-xs text-ink-2">
                Max per artist
                <select value={maxPerArtist} onChange={(e) => setMaxPerArtist(+e.target.value)} className="rounded border border-line bg-surface px-1 py-0.5">
                  {[1, 2, 3].map((n) => <option key={n}>{n}</option>)}
                </select>
              </label>
            </div>
          </Panel>

          <button
            onClick={build}
            disabled={waypoints.length < 2 || busy}
            className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {busy ? "Building…" : waypoints.length < 2 ? "Add at least two songs" : "Build the bridge"}
          </button>
          {error && <p className="text-sm" style={{ color: "var(--critical)" }}>{error}</p>}
        </aside>

        <div className="min-w-0 space-y-5">
          {!bridge ? (
            <div className="flex h-full min-h-80 items-center justify-center rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">
              Your bridge will appear here: the track list, how energy and mood move through every song, and why each seam works.
            </div>
          ) : (
            <>
              <Panel
                title="The bridge"
                note={`${bridge.tracks.length} tracks · seam cost ${(bridge.total_cost / bridge.transitions.length).toFixed(2)} avg · ${Math.round(bridge.confidence * 100)}% full-song analyzed`}
              >
                <ol className="divide-y divide-line">
                  {bridge.tracks.map((t, i) => (
                    <li key={t.id} className="flex items-center gap-3 py-2 text-sm">
                      <span className="tabular w-5 text-xs text-muted">{i + 1}</span>
                      <button
                        onClick={() => play(t.id, t.preview_url)}
                        disabled={!t.preview_url}
                        aria-label={playing === t.id ? "Stop preview" : "Play preview"}
                        className="flex size-7 shrink-0 items-center justify-center rounded-full border border-line text-xs disabled:opacity-30"
                      >
                        {playing === t.id ? "■" : "▶"}
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="truncate">
                          <span className={t.role === "waypoint" ? "font-semibold" : ""}>{t.title}</span>
                          <span className="text-muted"> — {t.artist}</span>
                        </div>
                        <div className="tabular text-xs text-muted">
                          {Math.round(t.bpm)} BPM · {t.camelot} · energy {t.energy.toFixed(2)}
                          {t.role === "waypoint" && <span className="ml-2 rounded bg-grid/60 px-1.5 py-px text-ink-2">waypoint</span>}
                          {t.tier === "B" && <span className="ml-2 rounded border border-line px-1.5 py-px">preview-only</span>}
                        </div>
                      </div>
                      {i < bridge.transitions.length && (
                        <div className="flex shrink-0 items-center gap-1 text-xs" title="Rate the transition into the next track">
                          <span className="tabular mr-1 text-muted">→ {bridge.transitions[i].cost.toFixed(2)}</span>
                          <button
                            aria-label="Good transition"
                            onClick={() => rate(i, 1)}
                            className={`rounded px-1.5 py-0.5 ${ratings[i] === 1 ? "bg-accent text-white" : "text-muted hover:text-ink"}`}
                          >
                            ↑
                          </button>
                          <button
                            aria-label="Bad transition"
                            onClick={() => rate(i, -1)}
                            className={`rounded px-1.5 py-0.5 ${ratings[i] === -1 ? "bg-ink text-surface" : "text-muted hover:text-ink"}`}
                          >
                            ↓
                          </button>
                        </div>
                      )}
                    </li>
                  ))}
                </ol>
                <div className="mt-3 flex gap-4 text-xs">
                  <a href={api.m3uUrl(bridge.id)} className="text-accent hover:underline">Export M3U</a>
                </div>
              </Panel>

              <Panel title="Energy & mood through every song" note="hover to inspect">
                <TrajectoryChart tracks={bridge.tracks} details={details} />
              </Panel>

              <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_auto]">
                <Panel title="Why each seam works" note="lower is smoother">
                  <TransitionBars tracks={bridge.tracks} transitions={bridge.transitions} />
                </Panel>
                <Panel title="Harmonic route">
                  <CamelotWheel tracks={bridge.tracks} />
                </Panel>
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
