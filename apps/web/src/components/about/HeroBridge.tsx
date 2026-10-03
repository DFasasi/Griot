"use client";

/**
 * Hero animation: song A's waveform winds down as song B's rises, meeting at a glowing seam,
 * while a playhead sweeps across. Pure SVG + CSS; static when reduced motion is requested.
 */

const N = 56;

function bars(seed: number, env: (x: number) => number) {
  let s = seed;
  const r = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  return Array.from({ length: N }, (_, i) => {
    const x = i / (N - 1);
    return Math.max(0.06, env(x) * (0.55 + 0.45 * r()));
  });
}

const A = bars(7, (x) => (x < 0.7 ? 0.85 - 0.15 * Math.sin(x * 9) : 0.85 * (1 - (x - 0.7) / 0.3) + 0.1));
const B = bars(19, (x) => (x > 0.3 ? 0.9 - 0.12 * Math.cos(x * 8) : 0.12 + 0.75 * (x / 0.3)));

export function HeroBridge() {
  const W = 1000;
  const H = 220;
  const mid = H / 2;
  const half = W / 2;
  const bw = half / N;
  const overlap = 120; // the crossfade region where A's outro meets B's intro
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Song A fading into song B at the seam">
        <defs>
          <linearGradient id="fadeA" x1="0" x2="1">
            <stop offset="0" stopColor="var(--s1)" stopOpacity="0.95" />
            <stop offset="1" stopColor="var(--s1)" stopOpacity="0.35" />
          </linearGradient>
          <linearGradient id="fadeB" x1="0" x2="1">
            <stop offset="0" stopColor="var(--gold)" stopOpacity="0.35" />
            <stop offset="1" stopColor="var(--gold)" stopOpacity="0.95" />
          </linearGradient>
          <radialGradient id="seamGlow">
            <stop offset="0" stopColor="var(--accent-text)" stopOpacity="0.55" />
            <stop offset="1" stopColor="var(--accent-text)" stopOpacity="0" />
          </radialGradient>
        </defs>
        <ellipse cx={half} cy={mid} rx={overlap} ry={H / 2} fill="url(#seamGlow)" className="seam-pulse" />
        {A.map((a, i) => {
          const x = i * bw + overlap / 2 - 10;
          const h = a * (H * 0.8);
          return (
            <rect key={`a${i}`} x={x} y={mid - h / 2} width={bw * 0.55} height={h} rx={bw * 0.27} fill="url(#fadeA)"
              className="hero-bar" style={{ animationDelay: `${(i % 9) * -0.17}s` }} />
          );
        })}
        {B.map((b, i) => {
          const x = half - overlap / 2 + 10 + i * bw;
          const h = b * (H * 0.8);
          return (
            <rect key={`b${i}`} x={x} y={mid - h / 2} width={bw * 0.55} height={h} rx={bw * 0.27} fill="url(#fadeB)"
              className="hero-bar" style={{ animationDelay: `${(i % 7) * -0.21}s` }} />
          );
        })}
        <line x1={half} x2={half} y1={8} y2={H - 8} stroke="var(--accent-text)" strokeDasharray="4 6" strokeWidth={1.5} />
        <g className="playhead">
          <line x1={0} x2={0} y1={0} y2={H} stroke="var(--ink)" strokeOpacity="0.55" strokeWidth={1.5} />
          <circle cx={0} cy={6} r={4} fill="var(--ink)" />
        </g>
      </svg>
      <div className="mt-3 flex justify-between font-mono text-[11px] uppercase tracking-widest text-muted">
        <span>Song A · outro</span>
        <span className="text-accent">the seam</span>
        <span>Song B · intro</span>
      </div>
    </div>
  );
}
