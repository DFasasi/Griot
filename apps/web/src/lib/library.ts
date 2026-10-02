"use client";

import { api, type ImportItem, type Resolution } from "./api";

// The imported library lives on this device (localStorage): no account system needed yet.
const KEY = "griot.library.v1";

export type LibraryEntry = Resolution & { origin: string; added: number };

// Matching is rate-limited by Deezer (~2 calls per song), so keep each request short enough
// for any proxy in front of the API (dev server, desktop agent, hosting) and update progress often.
const BATCH = 20;

const itemKey = (i: ImportItem) => `${i.source}:${i.source_id ?? `${i.artist}|${i.title}`}`;

export function loadLibrary(): LibraryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "[]");
  } catch {
    return [];
  }
}

export function saveLibrary(entries: LibraryEntry[]) {
  localStorage.setItem(KEY, JSON.stringify(entries));
}

/** Resolve items in batches and merge them into the stored library (deduped by source id). */
export async function importItems(
  items: ImportItem[],
  origin: string,
  onProgress?: (done: number, total: number) => void,
): Promise<LibraryEntry[]> {
  const existing = new Map(loadLibrary().map((e) => [itemKey(e.item), e]));
  const fresh = items.filter((i) => !existing.has(itemKey(i)));
  for (let i = 0; i < fresh.length; i += BATCH) {
    // One retry per batch; anything already matched is saved, so re-importing resumes.
    const res = await api.resolve(fresh.slice(i, i + BATCH)).catch(() => api.resolve(fresh.slice(i, i + BATCH)));
    res.forEach((r) => existing.set(itemKey(r.item), { ...r, origin, added: Date.now() }));
    saveLibrary([...existing.values()]);
    onProgress?.(Math.min(i + BATCH, fresh.length), fresh.length);
  }
  return [...existing.values()];
}

/** Re-check coverage (songs get analysed over time). */
export async function refreshLibrary(onProgress?: (done: number, total: number) => void) {
  const entries = loadLibrary();
  const out: LibraryEntry[] = [];
  for (let i = 0; i < entries.length; i += BATCH) {
    const chunk = entries.slice(i, i + BATCH);
    const res = await api.resolve(chunk.map((e) => e.item));
    res.forEach((r, j) => out.push({ ...chunk[j], ...r }));
    onProgress?.(Math.min(i + BATCH, entries.length), entries.length);
  }
  saveLibrary(out);
  return out;
}

const WP_KEY = "griot.waypoints.v1";
export type PendingWaypoint = { id: string; title: string; artist: string };

export function queueWaypoint(w: PendingWaypoint) {
  const cur: PendingWaypoint[] = JSON.parse(localStorage.getItem(WP_KEY) ?? "[]");
  if (!cur.some((c) => c.id === w.id)) localStorage.setItem(WP_KEY, JSON.stringify([...cur, w]));
}

export function takeQueuedWaypoints(): PendingWaypoint[] {
  const cur: PendingWaypoint[] = JSON.parse(localStorage.getItem(WP_KEY) ?? "[]");
  localStorage.removeItem(WP_KEY);
  return cur;
}

/** track_id -> YouTube video id, for songs the user imported from YouTube (full-length playback). */
export function youtubeIds(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of loadLibrary())
    if (e.track_id && e.item.source === "youtube" && e.item.source_id) out[e.track_id] = e.item.source_id;
  return out;
}
