"use client";

import { useEffect, useState } from "react";

// The desktop app's local agent answers on the same origin under /agent. On the web it
// doesn't exist, which is how the UI knows which features to offer.

export type AgentJob = {
  kind: "idle" | "scanning" | "analyzing" | "submitting";
  total: number;
  done: number;
  current: string;
  errors: string[];
  per_track_s: number | null;
};

export type AgentStatus = {
  desktop: true;
  job: AgentJob;
  counts: Record<string, number>;
  api: string;
};

export type LocalTrack = {
  sha1: string;
  title: string;
  artist: string;
  status: "pending" | "done" | "failed" | "skipped";
  submitted: boolean;
  duration_s: number | null;
  isrc: string | null;
  bpm?: number;
  camelot?: string;
  quality?: { bitrate_kbps?: string; cutoff_khz?: string; flags?: string };
};

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(`/agent${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
  return r.json();
}

export const agent = {
  status: () => call<AgentStatus>("/status"),
  scan: (path?: string) => call("/scan", "POST", { path: path ?? null }),
  analyze: () => call("/analyze", "POST"),
  submit: () => call("/submit", "POST"),
  stop: () => call("/stop", "POST"),
  library: () => call<LocalTrack[]>("/library"),
};

/** null while checking, then true (desktop app) or false (web). */
export function useDesktop(): boolean | null {
  const [desktop, setDesktop] = useState<boolean | null>(null);
  useEffect(() => {
    agent
      .status()
      .then((s) => setDesktop(s.desktop === true))
      .catch(() => setDesktop(false));
  }, []);
  return desktop;
}
