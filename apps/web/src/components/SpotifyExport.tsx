"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Bridge } from "@/lib/api";
import { spotifyIds } from "@/lib/library";
import { exportToSpotify, spotifyConnected, spotifyNeedsReconnectForExport, startSpotifyLogin } from "@/lib/spotify";

/** Save a bridge as a private Spotify playlist, in order. */
export function SpotifyExport({ bridge }: { bridge: Bridge }) {
  const [state, setState] = useState<"checking" | "disconnected" | "reconnect" | "ready">("checking");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ url: string; added: number; missing: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const h = setTimeout(() => {
      setState(!spotifyConnected() ? "disconnected" : spotifyNeedsReconnectForExport() ? "reconnect" : "ready");
    }, 0);
    return () => clearTimeout(h);
  }, []);

  useEffect(() => {
    const h = setTimeout(() => setResult(null), 0);
    return () => clearTimeout(h);
  }, [bridge.id]);

  const reconnect = async () => {
    const { spotify_client_id } = await api.config();
    if (spotify_client_id) startSpotifyLogin(spotify_client_id);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const ids = await spotifyIds();
      const first = bridge.tracks[0];
      const last = bridge.tracks[bridge.tracks.length - 1];
      setResult(
        await exportToSpotify(
          `Griot · ${first.title} → ${last.title}`,
          `A Griot bridge from ${first.artist} to ${last.artist} through ${bridge.tracks.length - 2} songs, ordered so every ending flows into the next beginning.`,
          bridge.tracks.map((t) => ({ title: t.title, artist: t.artist, spotifyId: ids[t.id] ?? null })),
        ),
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      if (msg.includes("reconnect")) setState("reconnect");
    } finally {
      setBusy(false);
    }
  };

  if (state === "checking") return null;
  if (state === "disconnected")
    return (
      <Link href="/library" className="text-muted hover:text-ink">
        Connect Spotify in Library to save playlists
      </Link>
    );
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {state === "reconnect" ? (
        <button className="text-accent hover:underline" onClick={reconnect}>
          Reconnect Spotify to save playlists
        </button>
      ) : result ? (
        <span>
          Saved {result.added}/{bridge.tracks.length} to Spotify ·{" "}
          <a href={result.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
            Open in Spotify ↗
          </a>
          {result.missing.length > 0 && (
            <span className="text-muted" title={result.missing.join("\n")}>
              {" "}
              · {result.missing.length} not on Spotify
            </span>
          )}
        </span>
      ) : (
        <button className="text-accent hover:underline disabled:opacity-50" disabled={busy} onClick={save}>
          {busy ? "Saving to Spotify…" : "Save to Spotify"}
        </button>
      )}
      {error && <span style={{ color: "var(--critical)" }}>{error}</span>}
    </span>
  );
}
