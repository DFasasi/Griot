"use client";

import { stage, useReducedMotion, useScrollProgress } from "@/components/motion";

/**
 * Sticky scroll story: as the reader scrolls, one song's waveform draws itself, splits into
 * its sections, shows what a 30 s preview would capture, then lights up the ending and the
 * next song's beginning — the seam Griot actually compares.
 */

const SECTIONS = [
  { label: "intro", len: 14, level: 0.3 },
  { label: "verse", len: 30, level: 0.55 },
  { label: "chorus", len: 24, level: 0.85 },
  { label: "verse", len: 28, level: 0.6 },
  { label: "chorus", len: 24, level: 0.9 },
  { label: "bridge", len: 18, level: 0.45 },
  { label: "chorus", len: 26, level: 0.95 },
  { label: "outro", len: 22, level: 0.28 },
];
const TOTAL = SECTIONS.reduce((n, s) => n + s.len, 0);
const PREVIEW = [SECTIONS.slice(0, 4).reduce((n, s) => n + s.len, 0), 0]; // second chorus
PREVIEW[1] = PREVIEW[0] + 24;

const BARS = (() => {
  let s = 11;
  const r = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  const out: { t: number; v: number; sec: number }[] = [];
  let t = 0;
  SECTIONS.forEach((sec, si) => {
    for (let k = 0; k < sec.len; k += 1.6) out.push({ t: t + k, v: sec.level * (0.6 + 0.4 * r()), sec: si });
    t += sec.len;
  });
  return out;
})();

const STEPS = [
  {
    title: "Every song is a journey",
    body: "Griot listens to the whole song, second by second: how loud it is, where the beats fall, which key it's in, how the energy and mood move.",
  },
  {
    title: "It finds the shape",
    body: "A structure model maps the sections — intro, verses, choruses, bridge, outro — with exact timestamps, so Griot knows how a song is built.",
  },
  {
    title: "A preview sees one moment",
    body: "Streaming services share a 30-second clip, usually the hook. From that alone, the way a song starts and ends is invisible.",
  },
  {
    title: "Griot listens where songs meet",
    body: "It compares how each song ends with how the next one begins: sound, tempo, key, energy and mood at the seam you actually hear.",
  },
];

export function AnatomyStory() {
  const [ref, raw] = useScrollProgress<HTMLDivElement>();
  const reduce = useReducedMotion();
  const p = reduce ? 1 : raw;
  const draw = stage(p, 0.02, 0.24);
  const label = stage(p, 0.24, 0.46);
  const preview = stage(p, 0.48, 0.58) * (1 - stage(p, 0.72, 0.78));
  const seam = stage(p, 0.76, 0.92);
  const step = Math.min(3, Math.floor(stage(p, 0, 0.98) * 4));

  const W = 640;
  const H = 300;
  const mid = 128;
  const x = (t: number) => 20 + (t / (TOTAL + 40)) * (W - 40);
  const nextStart = TOTAL + 6;

  return (
    <div ref={ref} className="relative" style={{ height: reduce ? "auto" : "320vh" }}>
      <div className={`${reduce ? "" : "sticky top-16"} flex min-h-[calc(100vh-4rem)] flex-col justify-center py-10`}>
        <div className="grid items-center gap-8 lg:grid-cols-[1.5fr_1fr]">
          <div className="rounded-2xl border border-line bg-surface p-4 sm:p-6">
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="A song's waveform, its sections, and the seam with the next song">
              <defs>
                <clipPath id="drawClip">
                  <rect x={0} y={0} width={20 + draw * (x(TOTAL) - 20) + 2} height={H} />
                </clipPath>
              </defs>
              {/* section bands */}
              {SECTIONS.map((s, i) => {
                const t0 = SECTIONS.slice(0, i).reduce((n, q) => n + q.len, 0);
                const shown = stage(label, i / SECTIONS.length, (i + 1) / SECTIONS.length);
                return (
                  <g key={i} opacity={shown}>
                    <rect x={x(t0) + 1} y={30} width={x(t0 + s.len) - x(t0) - 2} height={H - 110} rx={6}
                      fill={s.label === "chorus" ? "var(--accent)" : "var(--ink)"} fillOpacity={s.label === "chorus" ? 0.1 : 0.035} />
                    <text x={(x(t0) + x(t0 + s.len)) / 2} y={H - 60} textAnchor="middle" fontSize={12} fill="var(--ink-2)">
                      {s.label}
                    </text>
                  </g>
                );
              })}
              {/* waveform */}
              <g clipPath="url(#drawClip)">
                {BARS.map((b, i) => {
                  const h = b.v * 170;
                  const inPreview = b.t >= PREVIEW[0] && b.t <= PREVIEW[1];
                  const edge = b.sec === 0 || b.sec === SECTIONS.length - 1;
                  const dim = Math.max(preview * (inPreview ? 0 : 0.78), seam * (edge ? 0 : 0.6));
                  return (
                    <rect key={i} x={x(b.t)} y={mid - h / 2} width={3.6} height={h} rx={1.8}
                      fill={edge && seam > 0 ? "var(--accent-text)" : "var(--s1)"} opacity={1 - dim} />
                  );
                })}
              </g>
              {/* preview window */}
              <g opacity={preview}>
                <rect x={x(PREVIEW[0]) - 4} y={24} width={x(PREVIEW[1]) - x(PREVIEW[0]) + 10} height={H - 98} rx={10}
                  fill="none" stroke="var(--gold)" strokeWidth={2.5} />
                <text x={(x(PREVIEW[0]) + x(PREVIEW[1])) / 2} y={H - 26} textAnchor="middle" fontSize={14} fontWeight={600} fill="var(--gold)">
                  30-second preview
                </text>
              </g>
              {/* the next song arriving, and the seam */}
              <g opacity={seam}>
                {Array.from({ length: 10 }, (_, i) => {
                  const h = (0.18 + i * 0.05) * 170;
                  return <rect key={i} x={x(nextStart + i * 1.6) + (1 - seam) * 30} y={mid - h / 2} width={3.6} height={h} rx={1.8} fill="var(--gold)" />;
                })}
                <line x1={x(TOTAL + 3)} x2={x(TOTAL + 3)} y1={26} y2={H - 80} stroke="var(--accent-text)" strokeWidth={2} strokeDasharray="5 6" />
                <text x={x(TOTAL + 3)} y={16} textAnchor="end" fontSize={14} fontWeight={600} fill="var(--accent-text)">
                  the seam →
                </text>
                <text x={x(0)} y={H - 26} textAnchor="start" fontSize={13} fill="var(--accent-text)">
                  ↑ how it begins
                </text>
                <text x={x(TOTAL)} y={H - 26} textAnchor="end" fontSize={13} fill="var(--accent-text)">
                  how it ends ↑
                </text>
              </g>
            </svg>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-grid">
              <div className="h-full rounded-full bg-accent" style={{ width: `${p * 100}%` }} />
            </div>
          </div>
          <ol className="space-y-5">
            {STEPS.map((s, i) => (
              <li
                key={s.title}
                className="border-l-2 pl-4 transition-all duration-500"
                style={{
                  borderColor: i === step ? "var(--accent-text)" : "var(--border)",
                  opacity: reduce || i === step ? 1 : 0.35,
                }}
              >
                <p className="font-mono text-[11px] uppercase tracking-widest text-muted">0{i + 1}</p>
                <h3 className="font-display text-xl">{s.title}</h3>
                <p className="mt-1 text-sm leading-relaxed text-ink-2">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
