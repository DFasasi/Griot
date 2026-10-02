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

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
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
  m3uUrl: (id: string) => `/api/bridges/${id}/m3u`,
};

/** Same mapping as griot_core.catalog.norm_lufs. */
export const normLufs = (x: number) => Math.min(1, Math.max(0, (x + 24) / 20));

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
