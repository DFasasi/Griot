"use client";

import type { ImportItem } from "./api";

// Authorization Code + PKCE: no client secret, so it runs entirely in the browser/desktop
// webview. Spotify requires loopback redirects to use 127.0.0.1 (not "localhost").
// Since Feb 2026, dev-mode track objects carry no ISRC — the resolver matches by
// title/artist/duration instead.

const SCOPES = "user-library-read playlist-read-private playlist-read-collaborative";
const AUTH = "https://accounts.spotify.com/authorize";
const TOKEN = "https://accounts.spotify.com/api/token";
const API = "https://api.spotify.com/v1";
const KEY = "griot.spotify";

const redirectUri = () => `${window.location.origin}/library`;

function b64url(bytes: ArrayBuffer | Uint8Array) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function startSpotifyLogin(clientId: string) {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(48)));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(12)));
  sessionStorage.setItem("griot.spotify.pkce", JSON.stringify({ verifier, state, clientId }));
  const q = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirectUri(),
    code_challenge_method: "S256",
    code_challenge: challenge,
    scope: SCOPES,
    state,
  });
  // External navigation to Spotify's consent page, not an internal route.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.href = `${AUTH}?${q}`;
}

type Token = { access_token: string; refresh_token?: string; expires_at: number; client_id: string };

/** Completes the redirect leg if the URL carries ?code=…; returns true when a token was stored. */
export async function finishSpotifyLogin(): Promise<boolean> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const saved = sessionStorage.getItem("griot.spotify.pkce");
  if (!code || !saved) return false;
  const { verifier, state, clientId } = JSON.parse(saved);
  window.history.replaceState(null, "", window.location.pathname);
  sessionStorage.removeItem("griot.spotify.pkce");
  if (params.get("state") !== state) throw new Error("Spotify login state mismatch — try again");
  const r = await fetch(TOKEN, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri(),
      client_id: clientId,
      code_verifier: verifier,
    }),
  });
  if (!r.ok) throw new Error(`Spotify token exchange failed (${r.status})`);
  const t = await r.json();
  saveToken({ ...t, expires_at: Date.now() + t.expires_in * 1000, client_id: clientId });
  return true;
}

function saveToken(t: Token) {
  localStorage.setItem(KEY, JSON.stringify(t));
}

export function spotifyConnected() {
  return typeof window !== "undefined" && !!localStorage.getItem(KEY);
}

export function disconnectSpotify() {
  localStorage.removeItem(KEY);
}

async function token(): Promise<string> {
  const t: Token | null = JSON.parse(localStorage.getItem(KEY) ?? "null");
  if (!t) throw new Error("Spotify not connected");
  if (Date.now() < t.expires_at - 60_000) return t.access_token;
  if (!t.refresh_token) throw new Error("Spotify session expired — reconnect");
  const r = await fetch(TOKEN, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: t.refresh_token, client_id: t.client_id }),
  });
  if (!r.ok) {
    disconnectSpotify();
    throw new Error("Spotify session expired — reconnect");
  }
  const n = await r.json();
  saveToken({ ...t, ...n, expires_at: Date.now() + n.expires_in * 1000 });
  return n.access_token;
}

async function get(url: string) {
  const r = await fetch(url.startsWith("http") ? url : `${API}${url}`, {
    headers: { Authorization: `Bearer ${await token()}` },
  });
  if (r.status === 429) {
    await new Promise((res) => setTimeout(res, 1000 * Number(r.headers.get("retry-after") ?? 2)));
    return get(url);
  }
  if (!r.ok) throw new Error(`Spotify ${r.status} on ${url}`);
  return r.json();
}

type SpTrack = {
  id: string;
  name: string;
  duration_ms: number;
  artists: { name: string }[];
  album?: { name: string };
  type?: string;
  external_ids?: { isrc?: string };
};

const toItem = (t: SpTrack): ImportItem => ({
  source: "spotify",
  source_id: t.id,
  title: t.name,
  artist: t.artists.map((a) => a.name).join(", "),
  album: t.album?.name ?? null,
  duration_s: t.duration_ms / 1000,
  isrc: t.external_ids?.isrc ?? null, // present only for extended-quota apps
});

async function pages(first: string, pick: (el: Record<string, unknown>) => SpTrack | null, onProgress?: (n: number) => void) {
  const out: ImportItem[] = [];
  let next: string | null = first;
  while (next) {
    const page: { items: Record<string, unknown>[]; next: string | null } = await get(next);
    for (const el of page.items) {
      const t = pick(el);
      if (t && t.id && (t.type ?? "track") === "track") out.push(toItem(t));
    }
    onProgress?.(out.length);
    next = page.next;
  }
  return out;
}

export const spotify = {
  likedSongs: (onProgress?: (n: number) => void) =>
    pages("/me/tracks?limit=50", (el) => el.track as SpTrack, onProgress),
  playlists: async () => {
    const out: { id: string; name: string; count: number | null }[] = [];
    let next: string | null = "/me/playlists?limit=50";
    while (next) {
      type SpPlaylist = { id: string; name: string; items?: { total: number }; tracks?: { total: number } };
      const page: { items: SpPlaylist[]; next: string | null } = await get(next);
      for (const p of page.items)
        out.push({ id: p.id, name: p.name, count: (p.items ?? p.tracks)?.total ?? null });
      next = page.next;
    }
    return out;
  },
  // Feb 2026: /playlists/{id}/tracks -> /items, and each element's `track` -> `item`.
  playlistItems: (id: string, onProgress?: (n: number) => void) =>
    pages(`/playlists/${id}/items?limit=50`, (el) => ((el.item ?? el.track) as SpTrack) ?? null, onProgress),
};
