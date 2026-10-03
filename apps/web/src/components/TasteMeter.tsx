"use client";

import type { TuningStatus } from "@/lib/api";

/** Progress toward tuning the scoring to your ratings. */
export function TasteMeter({ status }: { status: TuningStatus | null }) {
  if (!status) return null;
  const pct = Math.min(100, (status.ratings / status.needed) * 100);
  const lacking = status.ratings >= status.needed && !status.ready;
  return (
    <section className="rounded-xl border border-line bg-surface p-4 text-sm">
      <div className="mb-1 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Teach Griot your taste</h2>
        <span className="tabular text-xs text-muted">
          {status.ratings} / {status.needed}
        </span>
      </div>
      <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-grid">
        <div className="h-full rounded-full bg-accent transition-all duration-700" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-ink-2">
        {status.ready ? (
          <>
            Enough ratings to tune. Run <code className="rounded bg-surface-2 px-1">uv run griot-enrich tune</code>; it only
            applies if the new scoring predicts your ratings better.
          </>
        ) : lacking ? (
          <>Rate a few more of both kinds: tuning needs at least {status.min_each} 👍 and {status.min_each} 👎.</>
        ) : (
          <>Rate transitions with ↑ / ↓ as you listen. Tuning unlocks at {status.needed}, and it learns from both good and bad seams.</>
        )}
      </p>
      <p className="tabular mt-1 text-xs text-muted">
        {status.up} 👍 · {status.down} 👎
        {status.last && <> · last tuned on {new Date(status.last.at).toLocaleDateString()} from {status.last.n} ratings</>}
      </p>
    </section>
  );
}
