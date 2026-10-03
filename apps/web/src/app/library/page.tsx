"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { agent, useDesktop, type AgentStatus, type LocalTrack } from "@/lib/agent";
import { api, type Coverage, type YouTubeLinkReport } from "@/lib/api";
import { importItems, loadLibrary, queueWaypoint, refreshLibrary, syncLibrary, type LibraryEntry } from "@/lib/library";
import { createSyncCode, getSyncCode, lastSynced, setSyncCode } from "@/lib/sync";
import { disconnectSpotify, finishSpotifyLogin, spotify, spotifyConnected, startSpotifyLogin } from "@/lib/spotify";

const COVERAGE: { key: Coverage; label: string; color: string; note: string }[] = [
  { key: "full", label: "Full song", color: "var(--s1)", note: "analysed end to end" },
  { key: "preview", label: "Preview only", color: "var(--s2)", note: "30 s stand-in until someone analyses the file" },
  { key: "missing", label: "Queued", color: "var(--s3)", note: "identified, waiting for analysis" },
  { key: "unmatched", label: "Not found", color: "var(--muted)", note: "couldn't identify the recording" },
];

function Card({ title, children, footer }: { title: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <section className="flex flex-col rounded-xl border border-line bg-surface p-4">
      <h2 className="mb-2 text-sm font-semibold">{title}</h2>
      <div className="flex-1 text-sm text-ink-2">{children}</div>
      {footer && <div className="mt-3">{footer}</div>}
    </section>
  );
}

const btn = "rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40";
const ghost = "rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-grid/40 disabled:opacity-40";

function CoverageBar({ entries }: { entries: LibraryEntry[] }) {
  const counts = COVERAGE.map((c) => ({ ...c, n: entries.filter((e) => e.status === c.key).length }));
  const total = entries.length || 1;
  const shown = counts.filter((c) => c.n > 0);
  return (
    <div>
      <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-grid/40" role="img" aria-label="Library coverage">
        {shown.map((c, i) => (
          <div
            key={c.key}
            title={`${c.label}: ${c.n}`}
            style={{ width: `${(c.n / total) * 100}%`, background: c.color }}
            className={i === shown.length - 1 ? "rounded-r-full" : ""}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
        {counts.map((c) => (
          <span key={c.key} className="flex items-center gap-1.5 text-ink-2" title={c.note}>
            <span className="inline-block size-2.5 rounded-sm" style={{ background: c.color }} />
            {c.label} <span className="tabular font-medium text-ink">{c.n}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function SpotifyCard({ onImported }: { onImported: () => void }) {
  const [clientId, setClientId] = useState<string | null | undefined>(undefined);
  const [connected, setConnected] = useState(false);
  const [playlists, setPlaylists] = useState<{ id: string; name: string; count: number | null }[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.config().then((c) => setClientId(c.spotify_client_id)).catch(() => setClientId(null));
    finishSpotifyLogin()
      .then(() => setConnected(spotifyConnected()))
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    if (connected) spotify.playlists().then(setPlaylists).catch((e) => setError(e.message));
  }, [connected]);

  const run = async (label: string, fetcher: (p: (n: number) => void) => Promise<Parameters<typeof importItems>[0]>) => {
    setBusy(`Reading ${label}…`);
    setError(null);
    try {
      const items = await fetcher((n) => setBusy(`Reading ${label}… ${n}`));
      await importItems(items, `Spotify · ${label}`, (d, t) => setBusy(`Matching ${d}/${t}…`));
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title="Spotify"
      footer={
        connected ? (
          <div className="flex flex-wrap gap-2">
            <button className={btn} disabled={!!busy} onClick={() => run("Liked Songs", spotify.likedSongs)}>
              Import Liked Songs
            </button>
            <button
              className={ghost}
              onClick={() => {
                disconnectSpotify();
                setConnected(false);
              }}
            >
              Disconnect
            </button>
          </div>
        ) : clientId ? (
          <button className={btn} onClick={() => startSpotifyLogin(clientId)}>
            Connect Spotify
          </button>
        ) : null
      }
    >
      <p>Imports which songs you love: titles and artists only. Spotify never shares audio, so analysis comes from files or the shared catalog.</p>
      {clientId === null && (
        <p className="mt-2 text-xs text-muted">Not configured: set SPOTIFY_CLIENT_ID on the API server.</p>
      )}
      {connected && playlists.length > 0 && (
        <ul className="mt-3 max-h-40 space-y-1 overflow-auto pr-1">
          {playlists.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-2">
              <span className="truncate">
                {p.name} <span className="tabular text-xs text-muted">{p.count ?? ""}</span>
              </span>
              <button
                className="shrink-0 text-xs text-accent hover:underline disabled:opacity-40"
                disabled={!!busy}
                onClick={() => run(p.name, (cb) => spotify.playlistItems(p.id, cb))}
              >
                Import
              </button>
            </li>
          ))}
        </ul>
      )}
      {busy && <p className="mt-2 text-xs text-ink">{busy}</p>}
      {error && <p className="mt-2 text-xs" style={{ color: "var(--critical)" }}>{error}</p>}
    </Card>
  );
}

function YouTubeCard({ onImported }: { onImported: () => void }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<YouTubeLinkReport[]>([]);
  useEffect(() => {
    api.config().then((c) => setEnabled(c.youtube_import)).catch(() => setEnabled(false));
  }, []);
  const links = text.split(/[\s,]+/).filter(Boolean);
  const go = async () => {
    const unique = [...new Set(links)];
    setError(null);
    setReport([]);
    // Small groups keep every request short (a playlist can take a few seconds), so no proxy
    // times out, progress stays live, and one bad group only fails its own links.
    const GROUP = 5;
    const reports: YouTubeLinkReport[] = [];
    const items: Parameters<typeof importItems>[0] = [];
    try {
      for (let i = 0; i < unique.length; i += GROUP) {
        const group = unique.slice(i, i + GROUP);
        setBusy(`Reading links ${Math.min(i + GROUP, unique.length)}/${unique.length}…`);
        try {
          const res = await api.youtubeLinks(group);
          reports.push(...res.links);
          items.push(...res.items);
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          reports.push(...group.map((link) => ({ link, kind: null, count: 0, error: `couldn't read (${msg})` })));
        }
        setReport([...reports]);
      }
      const seen = new Set<string>();
      const fresh = items.filter((it) => !seen.has(it.source_id ?? it.title) && seen.add(it.source_id ?? it.title));
      if (fresh.length) await importItems(fresh, "YouTube", (d, t) => setBusy(`Matching ${d}/${t}…`));
      // keep only the links that failed in the box, so they can be fixed and retried
      setText(reports.filter((l) => l.error).map((l) => l.link).join("\n"));
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card title="YouTube / YouTube Music">
      <p>Paste playlist or video links, one per line. Public and unlisted playlists work; Griot reads the titles and works out the actual recordings.</p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder={"https://youtube.com/playlist?list=…\nhttps://youtu.be/…"}
        className="mt-3 w-full resize-y rounded-lg border border-line bg-page px-2.5 py-1.5 text-sm placeholder:text-muted"
      />
      <div className="mt-2 flex items-center gap-2">
        <button className={btn} disabled={!links.length || !!busy || enabled === false} onClick={go}>
          Import{links.length > 1 ? ` ${new Set(links).size} links` : ""}
        </button>
        {busy && <span className="text-xs text-ink">{busy}</span>}
      </div>
      {report.length > 0 && (
        <ul className="mt-3 max-h-48 space-y-1 overflow-auto pr-1 text-xs">
          {report.map((r, i) => (
            <li key={i} className="flex items-baseline gap-2">
              <span style={{ color: r.error ? "var(--critical)" : "var(--good)" }}>{r.error ? "✕" : "✓"}</span>
              <span className="min-w-0 flex-1 truncate text-muted" title={r.link}>
                {r.link}
              </span>
              <span className="shrink-0 text-ink-2">
                {r.error ?? (r.kind === "playlist" ? `${r.count} videos` : "1 video")}
              </span>
            </li>
          ))}
        </ul>
      )}
      {enabled === false && <p className="mt-2 text-xs text-muted">Not configured: set YOUTUBE_API_KEY on the API server.</p>}
      {error && <p className="mt-2 text-xs" style={{ color: "var(--critical)" }}>{error}</p>}
    </Card>
  );
}

function FilesCard({ onSubmitted }: { onSubmitted: () => void }) {
  const desktop = useDesktop();
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [local, setLocal] = useState<LocalTrack[]>([]);
  const [error, setError] = useState<string | null>(null);
  const prevKind = useRef<string>("idle");

  const poll = useCallback(async () => {
    try {
      const s = await agent.status();
      setStatus(s);
      if (s.job.kind === "idle" && prevKind.current !== "idle") {
        const lib = await agent.library();
        setLocal(lib);
        if (prevKind.current === "submitting") {
          // Shared files join the library like any other source, with live coverage status.
          const items = lib
            .filter((t) => t.status === "done" && t.submitted)
            .map((t) => ({
              source: "file" as const,
              source_id: t.sha1,
              title: t.title,
              artist: t.artist,
              duration_s: t.duration_s,
              isrc: t.isrc,
            }));
          await importItems(items, "Your files");
          onSubmitted();
        }
      }
      prevKind.current = s.job.kind;
    } catch {}
  }, [onSubmitted]);

  useEffect(() => {
    if (!desktop) return;
    agent.library().then(setLocal).catch(() => {});
    const first = setTimeout(poll, 0);
    const h = setInterval(poll, 1500);
    return () => {
      clearTimeout(first);
      clearInterval(h);
    };
  }, [desktop, poll]);

  const act = (fn: () => Promise<unknown>) => () => {
    setError(null);
    fn().then(poll).catch((e) => setError(e.message));
  };

  if (desktop === null) return <Card title="Your music files">Checking…</Card>;
  if (!desktop)
    return (
      <Card title="Your music files">
        <p>
          Full-song analysis runs on your computer, so your audio never leaves it. Get the <span className="text-ink">Griot desktop app</span>, point it
          at a folder of music you own, and every song you analyse upgrades the shared catalog.
        </p>
        <p className="mt-2 text-xs text-muted">
          Developers: <code className="rounded bg-grid/50 px-1">uv run griot app</code> runs the same thing from source.
        </p>
      </Card>
    );

  const j = status?.job;
  const c = status?.counts ?? {};
  const flagged = local.filter((t) => t.quality?.flags && t.quality.flags !== "ok").length;
  return (
    <Card
      title="Your music files"
      footer={
        <div className="flex flex-wrap gap-2">
          <button className={ghost} disabled={j?.kind !== "idle"} onClick={act(() => agent.scan())}>
            Choose folder…
          </button>
          <button className={btn} disabled={j?.kind !== "idle" || !c.pending} onClick={act(agent.analyze)}>
            Analyse {c.pending ? `${c.pending} song${c.pending === 1 ? "" : "s"}` : ""}
          </button>
          <button className={ghost} disabled={j?.kind !== "idle" || !(c.done > (c.submitted ?? 0))} onClick={act(agent.submit)}>
            Share features
          </button>
          {j && j.kind !== "idle" && (
            <button className={ghost} onClick={act(agent.stop)}>
              Stop
            </button>
          )}
        </div>
      }
    >
      <p>Analysed on this computer. Only features (numbers) are shared, never audio. Use files you own.</p>
      <div className="tabular mt-2 grid grid-cols-4 gap-2 text-center text-xs">
        {[
          ["found", (c.pending ?? 0) + (c.done ?? 0) + (c.failed ?? 0)],
          ["analysed", c.done ?? 0],
          ["shared", c.submitted ?? 0],
          ["low quality", flagged],
        ].map(([k, v]) => (
          <div key={k} className="rounded-lg bg-page py-1.5">
            <div className="text-base font-medium text-ink">{v}</div>
            <div className="text-muted">{k}</div>
          </div>
        ))}
      </div>
      {j && j.kind !== "idle" && (
        <div className="mt-3">
          <div className="mb-1 flex justify-between text-xs">
            <span className="truncate">
              {j.kind} {j.current}
            </span>
            <span className="tabular shrink-0 text-muted">
              {j.done}/{j.total}
              {j.per_track_s ? ` · ${j.per_track_s}s/song` : ""}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-grid/50">
            <div className="h-full rounded-full bg-accent" style={{ width: `${j.total ? (j.done / j.total) * 100 : 0}%` }} />
          </div>
        </div>
      )}
      {(!!error || !!j?.errors?.length) && (
        <p className="mt-2 truncate text-xs" style={{ color: "var(--critical)" }} title={error ?? j?.errors?.join("\n")}>
          {error ?? j?.errors?.[j.errors.length - 1]?.split("\n")[0]}
        </p>
      )}
    </Card>
  );
}

function ago(t: number) {
  const s = Math.round((Date.now() - t) / 1000);
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
}

function SyncBar({ onSynced }: { onSynced: (e: LibraryEntry[]) => void }) {
  const [code, setCode] = useState<string | null | undefined>(undefined);
  const [last, setLast] = useState<{ at: number; count: number } | null>(null);
  const [reveal, setReveal] = useState(false);
  const [entering, setEntering] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const refreshMeta = useCallback(async () => {
    setCode((await getSyncCode()) ?? null);
    setLast((await lastSynced()) ?? null);
  }, []);

  const sync = useCallback(async () => {
    setBusy("Syncing…");
    setError(null);
    try {
      onSynced(await syncLibrary());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      refreshMeta();
    }
  }, [onSynced, refreshMeta]);

  useEffect(() => {
    getSyncCode()
      .then((c) => {
        setCode(c ?? null);
        if (c) sync();
        else refreshMeta();
      })
      .catch(() => setCode(null));
  }, [sync, refreshMeta]);

  const turnOn = async () => {
    setBusy("Creating your code…");
    setError(null);
    try {
      await setSyncCode(await createSyncCode());
      setReveal(true);
      await sync();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  };

  const useCode = async () => {
    await setSyncCode(typed);
    setEntering(false);
    setTyped("");
    await sync();
    if (!(await lastSynced())) await setSyncCode(null); // bad code: don't keep it
    refreshMeta();
  };

  if (code === undefined) return null;
  return (
    <section className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-surface px-4 py-3 text-sm">
      {code ? (
        <>
          <span className="flex items-center gap-2">
            <span className="inline-block size-2 rounded-full" style={{ background: "var(--good)" }} />
            <span className="font-medium">Sync on</span>
            <span className="text-muted">
              {busy ?? (last ? `${last.count.toLocaleString()} songs · synced ${ago(last.at)}` : "not synced yet")}
            </span>
          </span>
          <span className="tabular rounded-md bg-page px-2 py-0.5 font-mono text-xs tracking-wider">
            {reveal ? code : `${code.slice(0, 4)}-••••-••••-••••-••••`}
          </span>
          <button className="text-xs text-accent hover:underline" onClick={() => setReveal(!reveal)}>
            {reveal ? "Hide" : "Show code"}
          </button>
          <button
            className="text-xs text-accent hover:underline"
            onClick={() => {
              navigator.clipboard?.writeText(code).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              });
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <span className="ml-auto flex gap-3 text-xs">
            <button className="text-accent hover:underline disabled:opacity-40" disabled={!!busy} onClick={sync}>
              Sync now
            </button>
            <button
              className="text-muted hover:text-ink"
              onClick={async () => {
                await setSyncCode(null);
                refreshMeta();
              }}
              title="Stops syncing in this browser. Your songs stay here and in the synced library."
            >
              Turn off here
            </button>
          </span>
          {reveal && (
            <p className="w-full text-xs text-muted">
              Keep this code private: anyone with it can see this library. Enter it in another browser or the desktop app to use the same library.
            </p>
          )}
        </>
      ) : entering ? (
        <>
          <input
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="XXXX-XXXX-XXXX-XXXX-XXXX"
            className="w-72 rounded-lg border border-line bg-page px-2.5 py-1 font-mono text-sm tracking-wider placeholder:text-muted"
          />
          <button className={btn} disabled={typed.replace(/[^0-9a-z]/gi, "").length < 20 || !!busy} onClick={useCode}>
            Use this code
          </button>
          <button className="text-xs text-muted hover:text-ink" onClick={() => setEntering(false)}>
            Cancel
          </button>
        </>
      ) : (
        <>
          <span className="text-ink-2">Use this library in other browsers and the desktop app.</span>
          <span className="ml-auto flex gap-2">
            <button className={btn} disabled={!!busy} onClick={turnOn}>
              {busy ?? "Turn on sync"}
            </button>
            <button className={ghost} onClick={() => setEntering(true)}>
              I have a code
            </button>
          </span>
        </>
      )}
      {error && <p className="w-full text-xs" style={{ color: "var(--critical)" }}>{error}</p>}
    </section>
  );
}

export default function LibraryPage() {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [filter, setFilter] = useState<Coverage | "all">("all");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [sent, setSent] = useState<Set<string>>(new Set());

  const [storageError, setStorageError] = useState<string | null>(null);
  const reload = useCallback(() => {
    loadLibrary()
      .then(setEntries)
      .catch((e) => setStorageError(`This browser blocked local storage (${e?.message ?? e}). Private windows can do this.`));
  }, []);
  useEffect(() => {
    const h = setTimeout(reload, 0);
    return () => clearTimeout(h);
  }, [reload]);
  const refresh = useCallback(async () => {
    setBusy("Re-checking coverage…");
    try {
      setEntries(await refreshLibrary((d, t) => setBusy(`Re-checking ${d}/${t}…`)));
    } finally {
      setBusy(null);
    }
  }, []);

  const shown = useMemo(() => {
    const ql = q.toLowerCase();
    return entries
      .filter((e) => filter === "all" || e.status === filter)
      .filter((e) => !ql || `${e.title ?? e.item.title} ${e.artist ?? e.item.artist}`.toLowerCase().includes(ql))
      .sort((a, b) => b.added - a.added);
  }, [entries, filter, q]);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Your library</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-2">
          Bring in the songs you love from anywhere. Griot works out which recording each one is, and shows how much of it has been analysed as a full
          song.
        </p>
      </header>

      {storageError && (
        <p className="mb-4 rounded-lg border border-line bg-surface px-3 py-2 text-sm" style={{ color: "var(--critical)" }}>
          {storageError}
        </p>
      )}
      <SyncBar onSynced={setEntries} />
      <div className="mb-6 grid gap-4 md:grid-cols-3">
        <SpotifyCard onImported={reload} />
        <YouTubeCard onImported={reload} />
        <FilesCard onSubmitted={refresh} />
      </div>

      <section className="rounded-xl border border-line bg-surface p-4">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">
            Imported songs <span className="tabular font-normal text-muted">{entries.length}</span>
          </h2>
          <button className="text-xs text-accent hover:underline disabled:opacity-40" disabled={!entries.length || !!busy} onClick={refresh}>
            {busy ?? "Re-check coverage"}
          </button>
        </div>
        {entries.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted">Nothing imported yet. Connect Spotify or paste a YouTube playlist above.</p>
        ) : (
          <>
            <CoverageBar entries={entries} />
            <div className="mt-4 flex flex-wrap items-center gap-2">
              {(["all", ...COVERAGE.map((c) => c.key)] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setFilter(k)}
                  className={`rounded-full border px-2.5 py-0.5 text-xs ${filter === k ? "border-accent text-ink" : "border-line text-ink-2"}`}
                >
                  {k === "all" ? "All" : COVERAGE.find((c) => c.key === k)!.label}
                </button>
              ))}
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Filter…"
                className="ml-auto w-40 rounded-lg border border-line bg-page px-2.5 py-1 text-xs placeholder:text-muted"
              />
            </div>
            <ul className="mt-3 divide-y divide-line">
              {shown.slice(0, 300).map((e, i) => {
                const cov = COVERAGE.find((c) => c.key === e.status)!;
                return (
                  <li key={`${e.item.source}:${e.item.source_id}:${i}`} className="flex items-center gap-3 py-2 text-sm">
                    <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: cov.color }} title={cov.label} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate">
                        {e.title ?? e.item.title} <span className="text-muted">— {e.artist ?? e.item.artist}</span>
                      </div>
                      <div className="truncate text-xs text-muted">
                        {cov.label} · {e.origin}
                        {e.item.source === "youtube" && e.item.title !== e.title && <> · from “{e.item.title}”</>}
                      </div>
                    </div>
                    {e.track_id ? (
                      <button
                        className="shrink-0 text-xs text-accent hover:underline disabled:text-muted disabled:no-underline"
                        disabled={sent.has(e.track_id)}
                        onClick={() => {
                          queueWaypoint({ id: e.track_id!, title: e.title ?? e.item.title, artist: e.artist ?? "" });
                          setSent(new Set([...sent, e.track_id!]));
                        }}
                      >
                        {sent.has(e.track_id) ? "Added" : "Use as waypoint"}
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {shown.length > 300 && <p className="mt-2 text-xs text-muted">Showing 300 of {shown.length}.</p>}
            {sent.size > 0 && (
              <p className="mt-3 text-sm">
                <Link href="/" className="text-accent hover:underline">
                  Open the Studio with {sent.size} waypoint{sent.size > 1 ? "s" : ""} →
                </Link>
              </p>
            )}
          </>
        )}
      </section>
    </main>
  );
}
