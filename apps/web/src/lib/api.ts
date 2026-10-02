// Mirrors griot_core.schema (BridgeRequest / BridgeResponse) — keep in sync.

export type TrackHit = {
  id: string;
  title: string;
  artist: string;
  bpm: number;
  camelot: string;
  year: number | null;
  tier: string;
  tags: string[];
};

export type ArcPoint = { t: number; energy?: number; valence?: number };

export type BridgeRequest = {
  waypoints: string[];
  length: number;
  arc?: ArcPoint[] | null;
  prompt?: string | null;
  filters?: { min_playcount?: number | null; max_per_artist?: number; allow_explicit?: boolean };
};

export type BridgeTrack = {
  id: string;
  title: string;
  artist: string;
  role: "waypoint" | "bridge";
  bpm: number;
  camelot: string;
  energy: number;
  valence: number | null;
  tier: string;
  preview_url: string | null;
};

export type Transition = { from_id: string; to_id: string; cost: number; terms: Record<string, number> };

export type Bridge = {
  id: string;
  tracks: BridgeTrack[];
  transitions: Transition[];
  total_cost: number;
  confidence: number;
  weights: Record<string, number>;
};

export type TrackDetail = {
  id: string;
  duration_s: number;
  global: { bpm: number; lufs: number; valence: number | null };
  structure: { segments: { start: number; end: number; label: string }[] };
  trajectory: { hop_s: number; energy: number[]; valence: number[] };
};

/** "/api" in dev and on desktop (both proxy); a full URL for a static web host. */
export const API_BASE = process.env.NEXT_PUBLIC_GRIOT_API ?? "/api";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.status === 204 ? (undefined as T) : r.json();
}

export const api = {
  search: (q: string) => req<TrackHit[]>(`/search?q=${encodeURIComponent(q)}&limit=8`),
  track: (id: string) => req<TrackDetail>(`/tracks/${encodeURIComponent(id)}`),
  bridge: (body: BridgeRequest) => req<Bridge>("/bridges", { method: "POST", body: JSON.stringify(body) }),
  feedback: (id: string, position: number, rating: 1 | -1) =>
    req<void>(`/bridges/${id}/feedback`, { method: "POST", body: JSON.stringify({ position, rating }) }),
  m3uUrl: (id: string) => `${API_BASE}/bridges/${id}/m3u`,
  config: () => req<{ spotify_client_id: string | null; youtube_import: boolean }>("/config"),
  resolve: (items: ImportItem[]) => req<Resolution[]>("/resolve", { method: "POST", body: JSON.stringify(items) }),
  youtubePlaylist: (url: string) => req<ImportItem[]>(`/import/youtube?playlist=${encodeURIComponent(url)}`),
};

export type ImportItem = {
  source: "spotify" | "youtube" | "file" | "manual";
  source_id?: string | null;
  title: string;
  artist?: string | null;
  album?: string | null;
  duration_s?: number | null;
  isrc?: string | null;
};

export type Coverage = "full" | "preview" | "missing" | "unmatched";

export type Resolution = {
  item: ImportItem;
  status: Coverage;
  track_id: string | null;
  title: string | null;
  artist: string | null;
  isrc: string | null;
  deezer_id: string | null;
  preview_url: string | null;
  confidence: number;
};

/** Same mapping as griot_core.catalog.norm_lufs. */
export const normLufs = (x: number) => Math.min(1, Math.max(0, (x + 30) / 26));

export const TERMS = ["sound", "tempo", "key", "energy", "mood", "lyrics", "popularity"] as const;
export const TERM_LABEL: Record<string, string> = {
  sound: "Sound (outro→intro)",
  tempo: "Tempo",
  key: "Key",
  energy: "Energy",
  mood: "Mood",
  lyrics: "Lyrics",
  popularity: "Popularity",
};
