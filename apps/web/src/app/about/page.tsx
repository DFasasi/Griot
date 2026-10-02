"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useWidth } from "@/components/useWidth";

/* ------------------------------------------------------------------ shared bits */

function Section({ kicker, title, children }: { kicker: string; title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line py-12">
      <p className="mb-1 text-xs font-medium uppercase tracking-wider text-accent">{kicker}</p>
      <h2 className="mb-4 text-xl font-semibold tracking-tight">{title}</h2>
      {children}
    </section>
  );
}

function Toggle<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface p-0.5 text-sm">
      {options.map(([v, label]) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`rounded-md px-3 py-1 ${value === v ? "bg-accent text-white" : "text-ink-2 hover:text-ink"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

// Deterministic pseudo-random, so the illustrations are identical on every load.
function rng(seed: number) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

/* ------------------------------------------------------------------ 1. snippets vs full songs */

type Sec = { label: string; len: number; level: number };
const SONG_A: Sec[] = [
  { label: "intro", len: 16, level: 0.35 },
  { label: "verse", len: 40, level: 0.55 },
  { label: "chorus", len: 30, level: 0.85 },
  { label: "verse", len: 36, level: 0.6 },
  { label: "chorus", len: 32, level: 0.9 },
  { label: "outro", len: 26, level: 0.3 },
];
const SONG_B: Sec[] = [
  { label: "intro", len: 20, level: 0.32 },
  { label: "verse", len: 38, level: 0.58 },
  { label: "chorus", len: 30, level: 0.88 },
  { label: "bridge", len: 24, level: 0.5 },
  { label: "chorus", len: 34, level: 0.92 },
  { label: "outro", len: 18, level: 0.4 },
];

function curve(secs: Sec[], seed: number) {
  const r = rng(seed);
  const pts: { t: number; e: number; sec: string }[] = [];
  let t = 0;
  secs.forEach((s, i) => {
    const next = secs[i + 1]?.level ?? s.level * 0.5;
    for (let k = 0; k < s.len; k += 2) {
      const ramp = k / s.len > 0.8 ? (k / s.len - 0.8) / 0.2 : 0;
      pts.push({ t: t + k, e: s.level * (1 - ramp * 0.5) + next * ramp * 0.5 + (r() - 0.5) * 0.06, sec: s.label });
    }
    t += s.len;
  });
  return { pts, len: t };
}

function SnippetVsFull() {
  const [mode, setMode] = useState<"preview" | "full">("preview");
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ x: number; text: string } | null>(null);
  const a = useMemo(() => curve(SONG_A, 3), []);
  const b = useMemo(() => curve(SONG_B, 9), []);
  const H = 200;
  const M = { l: 8, r: 8, t: 24, b: 28 };
  const total = a.len + b.len;
  const x = (t: number) => M.l + (t / total) * (width - M.l - M.r);
  const y = (e: number) => M.t + (1 - e) * (H - M.t - M.b);
  // A typical preview: a 30 s cut of the hook, roughly in the middle.
  const winA = [96, 126];
  const winB = [a.len + 58, a.len + 88];
  const inWin = (t: number) => (t >= winA[0] && t <= winA[1]) || (t >= winB[0] && t <= winB[1]);
  const path = (pts: { t: number; e: number }[], off: number, only?: boolean) =>
    pts
      .filter((p) => !only || inWin(p.t + off))
      .map((p, i, arr) => `${i === 0 || (only && arr[i - 1] && p.t - arr[i - 1].t > 2) ? "M" : "L"}${x(p.t + off).toFixed(1)},${y(p.e).toFixed(1)}`)
      .join("");
  const seamX = x(a.len);
  const outroA = a.pts.slice(-6).reduce((s, p) => s + p.e, 0) / 6;
  const introB = b.pts.slice(0, 6).reduce((s, p) => s + p.e, 0) / 6;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Toggle
          value={mode}
          onChange={setMode}
          options={[
            ["preview", "What a 30 s preview sees"],
            ["full", "What Griot sees"],
          ]}
        />
      </div>
      <div ref={ref} className="relative rounded-xl border border-line bg-surface p-3">
        <svg
          width={width}
          height={H}
          role="img"
          aria-label="Energy of song A then song B"
          onPointerLeave={() => setHover(null)}
          onPointerMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const t = ((e.clientX - r.left - M.l) / (width - M.l - M.r)) * total;
            const inA = t < a.len;
            const pts = inA ? a.pts : b.pts;
            const local = inA ? t : t - a.len;
            const p = pts.reduce((best, q) => (Math.abs(q.t - local) < Math.abs(best.t - local) ? q : best), pts[0]);
            const visible = mode === "full" || inWin(t);
            setHover({
              x: x(t),
              text: visible
                ? `Song ${inA ? "A" : "B"} · ${p.sec} · ${Math.floor(local / 60)}:${String(Math.floor(local % 60)).padStart(2, "0")} · energy ${p.e.toFixed(2)}`
                : `Song ${inA ? "A" : "B"} · not in the preview — unknown`,
            });
          }}
        >
          <line x1={M.l} x2={width - M.r} y1={y(0)} y2={y(0)} stroke="var(--axis)" />
          {mode === "full" &&
            [...SONG_A.map((s, i) => ({ ...s, off: SONG_A.slice(0, i).reduce((n, q) => n + q.len, 0) })),
             ...SONG_B.map((s, i) => ({ ...s, off: a.len + SONG_B.slice(0, i).reduce((n, q) => n + q.len, 0) }))].map((s, i) => (
              <text key={i} x={x(s.off + s.len / 2)} y={H - 10} textAnchor="middle" fontSize={10} fill="var(--muted)">
                {width > 560 || s.label === "intro" || s.label === "outro" ? s.label : ""}
              </text>
            ))}
          {mode === "preview" &&
            [winA, winB].map(([s, e], i) => (
              <g key={i}>
                <rect x={x(s)} y={M.t} width={x(e) - x(s)} height={H - M.t - M.b} fill="var(--accent)" opacity={0.08} />
                <text x={x((s + e) / 2)} y={H - 10} textAnchor="middle" fontSize={10} fill="var(--ink-2)">
                  30 s preview
                </text>
              </g>
            ))}
          <line x1={seamX} x2={seamX} y1={M.t - 8} y2={H - M.b} stroke="var(--ink-2)" strokeDasharray="3 3" />
          <text x={seamX} y={M.t - 12} textAnchor="middle" fontSize={11} fill="var(--ink)" fontWeight={600}>
            the seam
          </text>
          <path d={path(a.pts, 0, mode === "preview")} fill="none" stroke="var(--s1)" strokeWidth={2} />
          <path d={path(b.pts, a.len, mode === "preview")} fill="none" stroke="var(--s2)" strokeWidth={2} />
          {mode === "preview" && (
            <text x={seamX} y={y(0.5)} textAnchor="middle" fontSize={28} fill="var(--muted)">
              ?
            </text>
          )}
          {mode === "full" && (
            <>
              <circle cx={x(a.len - 6)} cy={y(outroA)} r={5} fill="var(--s1)" stroke="var(--surface)" strokeWidth={2} />
              <circle cx={x(a.len + 6)} cy={y(introB)} r={5} fill="var(--s2)" stroke="var(--surface)" strokeWidth={2} />
            </>
          )}
          {hover && <line x1={hover.x} x2={hover.x} y1={M.t} y2={H - M.b} stroke="var(--axis)" pointerEvents="none" />}
        </svg>
        {hover && (
          <div
            className="pointer-events-none absolute top-2 rounded-md border border-line bg-surface px-2 py-1 text-xs shadow-sm"
            style={{ left: Math.min(hover.x + 16, width - 240) }}
          >
            {hover.text}
          </div>
        )}
        <div className="mt-2 flex gap-4 px-1 text-xs text-ink-2">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4" style={{ background: "var(--s1)" }} /> Song A energy
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4" style={{ background: "var(--s2)" }} /> Song B energy
          </span>
        </div>
      </div>
      <p className="mt-4 max-w-3xl text-sm text-ink-2">
        {mode === "preview" ? (
          <>
            Streaming services expose a 30-second clip, usually the loudest part. From the hook alone, A and B look like a fine pair, but nothing says how A
            <em> ends</em> or how B <em>begins</em>, which is the only part you actually hear when one song hands over to the next.
          </>
        ) : (
          <>
            Analysing the whole song reveals the structure: A winds down into a quiet outro (energy {outroA.toFixed(2)}), and B eases in at{" "}
            {introB.toFixed(2)}. Griot compares the <em>end</em> of every song with the <em>start</em> of the next: sound, tempo, key, energy and mood
            at the seam.
          </>
        )}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ 2. the pipeline */

const STEPS = [
  {
    name: "Listen",
    where: "On your computer",
    body: "The desktop app reads music files you own and analyses each one from first second to last: beats, sections (intro, verse, chorus, outro), key, tempo, loudness, and how energy and mood change over time.",
    detail: ["Beat & structure model", "Key & tempo", "Energy curve every 2 s", "Audio fingerprint → which recording it is"],
  },
  {
    name: "Understand",
    where: "On your computer",
    body: "A music AI model turns every 10 seconds of audio into a 'sound fingerprint'. Griot keeps separate fingerprints for each song's opening, ending, chorus and whole.",
    detail: ["512-number sound vectors", "Intro / outro / chorus / whole", "Mood estimates", "Only these numbers leave your machine"],
  },
  {
    name: "Connect",
    where: "Griot's catalog",
    body: "Features from everyone's analyses are pooled into a shared catalog, matched across Spotify, YouTube and MusicBrainz, with song lyrics summarised into themes. When the same song is analysed twice, the results are cross-checked.",
    detail: ["Cross-platform matching", "Popularity & lyrics", "Quality checks (wrong versions, bad rips)", "Full songs always outrank previews"],
  },
  {
    name: "Bridge",
    where: "Griot's catalog",
    body: "You choose waypoints. Griot searches for the sequence of songs that moves steadily from one to the next, scoring every seam, and shows you why each transition was chosen.",
    detail: ["Seam-by-seam scoring", "Steady progress to the target", "Energy arc & text steering", "Your 👍 / 👎 tune the scoring"],
  },
];

function Pipeline() {
  const [i, setI] = useState(0);
  const s = STEPS[i];
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {STEPS.map((st, k) => (
          <button
            key={st.name}
            onClick={() => setI(k)}
            className={`rounded-xl border p-3 text-left transition ${k === i ? "border-accent bg-surface shadow-sm" : "border-line hover:bg-surface"}`}
          >
            <div className="tabular text-xs text-muted">0{k + 1}</div>
            <div className="font-medium">{st.name}</div>
            <div className="text-xs text-muted">{st.where}</div>
          </button>
        ))}
      </div>
      <div className="mt-3 grid gap-4 rounded-xl border border-line bg-surface p-5 md:grid-cols-[1fr_260px]">
        <p className="text-sm leading-relaxed text-ink-2">{s.body}</p>
        <ul className="space-y-1.5 text-sm">
          {s.detail.map((d) => (
            <li key={d} className="flex gap-2">
              <span className="mt-1.5 inline-block size-1.5 shrink-0 rounded-full bg-accent" />
              {d}
            </li>
          ))}
        </ul>
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs text-muted">
        <span className="h-px flex-1 bg-line" />
        <span>
          {i < 2 ? "Your audio stays here" : "Only numbers arrive here, never audio"}
        </span>
        <span className="h-px flex-1 bg-line" />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 3. keys */

const MAJOR = ["B", "F♯", "D♭", "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E"];
const MINOR = ["A♭m", "E♭m", "B♭m", "Fm", "Cm", "Gm", "Dm", "Am", "Em", "Bm", "F♯m", "D♭m"];

function KeyWheel() {
  const [sel, setSel] = useState<{ n: number; minor: boolean }>({ n: 8, minor: true });
  const S = 300;
  const C = S / 2;
  const steps = (n: number, minor: boolean) => {
    const d = Math.abs(n - sel.n);
    return Math.min(d, 12 - d) + (minor !== sel.minor ? 1 : 0);
  };
  const fill = (st: number) => (st === 0 ? "var(--accent)" : st === 1 ? "var(--s3)" : st === 2 ? "var(--s4)" : "var(--grid)");
  const name = (n: number, minor: boolean) => (minor ? MINOR : MAJOR)[n - 1];
  return (
    <div className="grid items-center gap-6 md:grid-cols-[300px_1fr]">
      <svg width={S} height={S} viewBox={`0 0 ${S} ${S}`} role="img" aria-label="Camelot wheel; click a key">
        {[false, true].map((minor) =>
          Array.from({ length: 12 }, (_, i) => {
            const n = i + 1;
            const a0 = ((i - 0.5) / 12) * 2 * Math.PI - Math.PI / 2;
            const a1 = ((i + 0.5) / 12) * 2 * Math.PI - Math.PI / 2;
            const [r0, r1] = minor ? [62, 100] : [104, 142];
            // Round: Math.cos/sin can differ in the last bits between Node (SSR) and the browser.
            const p = (r: number, a: number) => `${(C + r * Math.cos(a)).toFixed(2)},${(C + r * Math.sin(a)).toFixed(2)}`;
            const d = `M${p(r0, a0)} L${p(r1, a0)} A${r1},${r1} 0 0 1 ${p(r1, a1)} L${p(r0, a1)} A${r0},${r0} 0 0 0 ${p(r0, a0)} Z`;
            const st = steps(n, minor);
            const am = (i / 12) * 2 * Math.PI - Math.PI / 2;
            const rm = (r0 + r1) / 2;
            return (
              <g key={`${minor}${n}`} onClick={() => setSel({ n, minor })} className="cursor-pointer">
                <path d={d} fill={fill(st)} stroke="var(--surface)" strokeWidth={2} opacity={st > 2 ? 0.6 : 1} />
                <text
                  x={(C + rm * Math.cos(am)).toFixed(2)}
                  y={(C + rm * Math.sin(am) + 4).toFixed(2)}
                  textAnchor="middle"
                  fontSize={10}
                  fill={st <= 1 ? "#fff" : "var(--ink-2)"}
                  pointerEvents="none"
                >
                  {name(n, minor)}
                </text>
              </g>
            );
          }),
        )}
        <text x={C} y={C - 2} textAnchor="middle" fontSize={13} fontWeight={600} fill="var(--ink)">
          {name(sel.n, sel.minor)}
        </text>
        <text x={C} y={C + 14} textAnchor="middle" fontSize={10} fill="var(--muted)">
          {sel.n}
          {sel.minor ? "A" : "B"}
        </text>
      </svg>
      <div className="text-sm text-ink-2">
        <p>
          Every key sits on the Camelot wheel. Songs in neighbouring keys share most of their notes, so moving one step (or between a major key and its
          relative minor) sounds natural, while jumping across the wheel clashes. <span className="text-ink">Click any key</span>.
        </p>
        <ul className="mt-4 space-y-2">
          {[
            ["var(--accent)", "Same key", "seamless"],
            ["var(--s3)", "One step away", "smooth: what DJs mix between"],
            ["var(--s4)", "Two steps", "noticeable lift; Griot allows it sparingly"],
            ["var(--grid)", "Further", "clashes; heavily penalised"],
          ].map(([c, a, b]) => (
            <li key={a} className="flex items-center gap-2">
              <span className="inline-block size-3 rounded-sm" style={{ background: c }} />
              <span className="text-ink">{a}</span> — {b}
            </li>
          ))}
        </ul>
        <p className="mt-4 text-xs text-muted">Tempo works the same way: within about 6% is effortless, and half- or double-time also lines up.</p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 4. route search, live */

type Cand = { e: number; k: number; sim: number };
const START: Cand = { e: 0.25, k: 8, sim: 1 };
const END: Cand = { e: 0.85, k: 10, sim: 1 };

function RouteDemo() {
  const [n, setN] = useState(5);
  const [mode, setMode] = useState<"griot" | "similar">("griot");
  const [ref, width] = useWidth<HTMLDivElement>();
  const PER = 7;
  const H = 260;
  const layers = useMemo(() => {
    const r = rng(42);
    return Array.from({ length: n }, (_, i) => {
      const target = 0.25 + (0.6 * (i + 1)) / (n + 1); // energy drifts from A toward B
      return Array.from({ length: PER }, () => ({
        e: Math.min(0.95, Math.max(0.05, target + (r() - 0.5) * 0.7)),
        k: Math.floor(r() * 12) + 1,
        sim: r(),
      })) as Cand[];
    });
  }, [n]);

  // A real (tiny) version of Griot's search: dynamic programming over the layers.
  const path = useMemo(() => {
    const keyCost = (a: number, b: number) => {
      const d = Math.abs(a - b);
      const s = Math.min(d, 12 - d);
      return [0, 0.15, 0.45, 0.7][s] ?? 1;
    };
    const seam = (a: Cand, b: Cand) => (mode === "griot" ? Math.abs(a.e - b.e) * 2 + keyCost(a.k, b.k) : 0);
    const node = (c: Cand) => (mode === "griot" ? 0.3 * (1 - c.sim) : 1 - c.sim);
    let best = layers[0].map((c) => ({ cost: seam(START, c) + node(c), path: [c] }));
    for (let i = 1; i < layers.length; i++) {
      best = layers[i].map((c) => {
        const prev = best.reduce((m, p) => {
          const v = p.cost + seam(p.path[p.path.length - 1], c);
          return v < m.v ? { v, p } : m;
        }, { v: Infinity, p: best[0] });
        return { cost: prev.v + node(c), path: [...prev.p.path, c] };
      });
    }
    return best.reduce((m, p) => {
      const v = p.cost + seam(p.path[p.path.length - 1], END);
      return v < m.v ? { v, p: p.path } : m;
    }, { v: Infinity, p: [] as Cand[] }).p;
  }, [layers, mode]);

  const cols = n + 2;
  const cx = (i: number) => 30 + (i / (cols - 1)) * (width - 60);
  const cy = (e: number) => 20 + (1 - e) * (H - 50);
  const full = [START, ...path, END];
  const jumps = full.slice(1).map((c, i) => Math.abs(c.e - full[i].e));
  const clashes = full.slice(1).filter((c, i) => {
    const d = Math.abs(c.k - full[i].k);
    return Math.min(d, 12 - d) >= 3;
  }).length;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Toggle
          value={mode}
          onChange={setMode}
          options={[
            ["griot", "Griot: score every seam"],
            ["similar", "Just pick similar songs"],
          ]}
        />
        <label className="flex items-center gap-2 text-sm text-ink-2">
          Bridge length
          <input type="range" min={3} max={8} value={n} onChange={(e) => setN(+e.target.value)} className="accent-[var(--accent)]" />
          <span className="tabular text-ink">{n}</span>
        </label>
      </div>
      <div ref={ref} className="rounded-xl border border-line bg-surface p-3">
        <svg width={width} height={H} role="img" aria-label="Candidate songs per step and the chosen route">
          <text x={8} y={14} fontSize={10} fill="var(--muted)">
            energy ↑
          </text>
          {layers.map((layer, i) =>
            layer.map((c, j) => (
              <circle key={`${i}-${j}`} cx={cx(i + 1)} cy={cy(c.e)} r={5} fill="var(--grid)" stroke="var(--axis)">
                <title>{`step ${i + 1} candidate · energy ${c.e.toFixed(2)} · key ${c.k} · similarity ${c.sim.toFixed(2)}`}</title>
              </circle>
            )),
          )}
          <polyline
            points={full.map((c, i) => `${cx(i)},${cy(c.e)}`).join(" ")}
            fill="none"
            stroke={mode === "griot" ? "var(--accent)" : "var(--s2)"}
            strokeWidth={2.5}
            strokeLinejoin="round"
          />
          {full.map((c, i) => (
            <circle
              key={i}
              cx={cx(i)}
              cy={cy(c.e)}
              r={i === 0 || i === full.length - 1 ? 8 : 6}
              fill={i === 0 || i === full.length - 1 ? "var(--ink)" : mode === "griot" ? "var(--accent)" : "var(--s2)"}
              stroke="var(--surface)"
              strokeWidth={2}
            >
              <title>{`${i === 0 ? "Waypoint A" : i === full.length - 1 ? "Waypoint B" : `Bridge song ${i}`} · energy ${c.e.toFixed(2)} · key ${c.k}`}</title>
            </circle>
          ))}
          <text x={cx(0)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--ink)">A</text>
          <text x={cx(cols - 1)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--ink)">B</text>
        </svg>
      </div>
      <div className="tabular mt-3 flex flex-wrap gap-6 text-sm">
        <span>
          Biggest energy jump <span className="font-medium text-ink">{Math.max(...jumps).toFixed(2)}</span>
        </span>
        <span>
          Key clashes <span className="font-medium text-ink">{clashes}</span>
        </span>
      </div>
      <p className="mt-3 max-w-3xl text-sm text-ink-2">
        Each column is a step in the bridge with a handful of candidate songs (the real thing considers about 200 per step). Griot weighs every possible
        hand-off and finds the best overall route in one pass, rather than greedily picking the next song. Switch to “just pick similar songs” to see the
        jumps and clashes that sneak in when seams are ignored.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

export default function About() {
  return (
    <main className="mx-auto max-w-5xl px-4 pb-20 sm:px-6">
      <section className="py-14">
        <h1 className="max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">An album that gets you from here to there, without the jolt.</h1>
        <p className="mt-4 max-w-2xl text-base text-ink-2">
          Pick two or more songs. Griot finds real tracks to put between them so the whole thing flows: every ending leads into the next beginning. Here
          is how it works.
        </p>
        <div className="mt-6 flex gap-3 text-sm">
          <Link href="/" className="rounded-lg bg-accent px-4 py-2 font-medium text-white">
            Build a bridge
          </Link>
          <Link href="/library" className="rounded-lg border border-line px-4 py-2 hover:bg-surface">
            Import your music
          </Link>
        </div>
      </section>

      <Section kicker="The idea" title="Full songs, not snippets">
        <SnippetVsFull />
      </Section>

      <Section kicker="Under the hood" title="Four steps from your music to a bridge">
        <Pipeline />
      </Section>

      <Section kicker="Harmony" title="Keys that belong together">
        <KeyWheel />
      </Section>

      <Section kicker="The search" title="Choosing the route">
        <RouteDemo />
      </Section>

      <Section kicker="Your data" title="Where songs come from, and what stays private">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="py-2 font-normal">Source</th>
                <th className="py-2 font-normal">What Griot uses</th>
                <th className="py-2 font-normal">What it never does</th>
              </tr>
            </thead>
            <tbody className="align-top text-ink-2">
              {[
                ["Your music files", "Analysed on your computer; only the resulting numbers are shared", "Upload your audio"],
                ["Spotify", "The list of songs you like and your playlists (titles and artists)", "Access or record Spotify audio"],
                ["YouTube", "Titles in a playlist you share, to identify the songs", "Download videos or audio"],
                ["Deezer", "30-second previews as a clearly marked stand-in until a full analysis exists", "Pretend a preview is the full song"],
                ["MusicBrainz, ListenBrainz, LRCLIB", "Open IDs, play counts, and lyrics (lyrics become themes; the text isn't stored)", "Store lyrics"],
              ].map(([a, b, c]) => (
                <tr key={a} className="border-t border-line">
                  <td className="py-2.5 pr-4 font-medium text-ink">{a}</td>
                  <td className="py-2.5 pr-4">{b}</td>
                  <td className="py-2.5">{c}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-4 max-w-3xl text-sm text-ink-2">
          Every song only needs to be analysed once, by anyone who owns it, for everyone to benefit. Until then Griot uses the preview and tells you so.
        </p>
      </Section>
    </main>
  );
}
