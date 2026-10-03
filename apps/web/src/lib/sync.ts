"use client";

import { API_BASE } from "./api";
import { kv } from "./store";
import type { LibraryEntry } from "./library";

// A private sync code links this browser's library to the same library elsewhere
// (another browser, the desktop app, another device once Griot is hosted).

const CODE_KEY = "griot.sync.code";
const LAST_KEY = "griot.sync.last";

export const getSyncCode = () => kv.get<string>(CODE_KEY);
export const lastSynced = () => kv.get<{ at: number; count: number }>(LAST_KEY);

export async function setSyncCode(code: string | null) {
  if (code) await kv.set(CODE_KEY, code.trim().toUpperCase());
  else {
    await kv.del(CODE_KEY);
    await kv.del(LAST_KEY);
  }
}

async function call<T>(path: string, init: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!r.ok) {
    const detail = await r.json().then((j) => j.detail).catch(() => r.statusText);
    throw new Error(typeof detail === "string" ? detail : `sync failed (${r.status})`);
  }
  return r.json();
}

export const createSyncCode = () => call<{ code: string }>("/sync/new", { method: "POST" }).then((r) => r.code);

/** Send this device's library, receive the union across every device using the code. */
export async function mergeWithServer(code: string, entries: LibraryEntry[]): Promise<LibraryEntry[]> {
  const res = await call<{ entries: LibraryEntry[]; count: number }>("/sync/merge", {
    method: "POST",
    headers: { "X-Griot-Sync": code },
    body: JSON.stringify({ entries }),
  });
  await kv.set(LAST_KEY, { at: Date.now(), count: res.count });
  return res.entries;
}
