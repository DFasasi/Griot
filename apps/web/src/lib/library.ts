"use client";

import { api, type ImportItem, type Resolution } from "./api";
import { kv } from "./store";
import { getSyncCode, mergeWithServer } from "./sync";

// The imported library lives on this device (IndexedDB): no account system needed yet.
const KEY = "griot.library.v1";

export type LibraryEntry = Resolution & { origin: string; added: number; checked?: number };

// Matching is rate-limited by Deezer (~2 calls per song), so keep each request short enough
// for any proxy in front of the API (dev server, desktop agent, hosting) and update progress often.
const BATCH = 20;

const itemKey = (i: ImportItem) => `${i.source}:${i.source_id ?? `${i.artist}|${i.title}`}`;

const LEGACY_KEY = KEY; // earlier builds kept the library in localStorage

/** The device-local library (IndexedDB). Moves an older localStorage copy over once. */
export async function loadLibrary(): Promise<LibraryEntry[]> {
  const stored = await kv.get<LibraryEntry[]>(KEY);
  if (stored) return stored;
  try {
    const legacy = localStorage.getItem(LEGACY_KEY);
    if (legacy) {
      const entries: LibraryEntry[] = JSON.parse(legacy);
      await kv.set(KEY, entries);
      localStorage.removeItem(LEGACY_KEY);
      return entries;
    }
  } catch {}
  return [];
}

export async function saveLibrary(entries: LibraryEntry[]) {
  await kv.set(KEY, entries);
}

/** If sync is on, merge this device's library with the synced one and store the result. */
export async function syncLibrary(): Promise<LibraryEntry[]> {
  const local = await loadLibrary();
  const code = await getSyncCode();
  if (!code) return local;
  const merged = await mergeWithServer(code, local);
  await saveLibrary(merged);
  return merged;
}

/** Resolve items in batches and merge them into the stored library (deduped by source id). */
export async function importItems(
  items: ImportItem[],
  origin: string,
  onProgress?: (done: number, total: number) => void,
): Promise<LibraryEntry[]> {
  const existing = new Map((await loadLibrary()).map((e) => [itemKey(e.item), e]));
  const fresh = items.filter((i) => !existing.has(itemKey(i)));
  for (let i = 0; i < fresh.length; i += BATCH) {
    // One retry per batch; anything already matched is saved, so re-importing resumes.
    const res = await api.resolve(fresh.slice(i, i + BATCH)).catch(() => api.resolve(fresh.slice(i, i + BATCH)));
    res.forEach((r) => existing.set(itemKey(r.item), { ...r, origin, added: Date.now(), checked: Date.now() }));
    await saveLibrary([...existing.values()]);
    onProgress?.(Math.min(i + BATCH, fresh.length), fresh.length);
  }
  return syncLibrary().catch(() => [...existing.values()]);
}

/** Re-check coverage (songs get analysed over time). */
export async function refreshLibrary(onProgress?: (done: number, total: number) => void) {
  const entries = await loadLibrary();
  const out: LibraryEntry[] = [];
  for (let i = 0; i < entries.length; i += BATCH) {
    const chunk = entries.slice(i, i + BATCH);
    const res = await api.resolve(chunk.map((e) => e.item));
    res.forEach((r, j) => out.push({ ...chunk[j], ...r, checked: Date.now() }));
    onProgress?.(Math.min(i + BATCH, entries.length), entries.length);
  }
  await saveLibrary(out);
  return syncLibrary().catch(() => out);
}

const WP_KEY = "griot.waypoints.v1";
export type PendingWaypoint = { id: string; title: string; artist: string };

export function queueWaypoint(w: PendingWaypoint) {
  try {
    const cur: PendingWaypoint[] = JSON.parse(localStorage.getItem(WP_KEY) ?? "[]");
    if (!cur.some((c) => c.id === w.id)) localStorage.setItem(WP_KEY, JSON.stringify([...cur, w]));
  } catch {} // storage blocked (e.g. private window): the hand-off is a convenience only
}

export function takeQueuedWaypoints(): PendingWaypoint[] {
  try {
    const cur: PendingWaypoint[] = JSON.parse(localStorage.getItem(WP_KEY) ?? "[]");
    localStorage.removeItem(WP_KEY);
    return cur;
  } catch {
    return [];
  }
}

/** track_id -> YouTube video id, for songs the user imported from YouTube (full-length playback). */
export async function youtubeIds(): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const e of await loadLibrary())
    if (e.track_id && e.item.source === "youtube" && e.item.source_id) out[e.track_id] = e.item.source_id;
  return out;
}
