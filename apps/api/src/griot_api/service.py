"""Bridge service: keeps an in-memory Catalog over the repo and answers bridge/search queries.

At MVP scale (≤ a few hundred thousand tracks) brute-force numpy over an in-memory catalog
is faster than a round trip to pgvector; the HNSW indexes take over once the catalog outgrows RAM.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

from griot_api.repo import Repo
from griot_core import Catalog, Pathfinder, allocate_gaps
from griot_core.catalog import norm_lufs
from griot_core.schema import BridgeRequest, BridgeResponse, BridgeTrack, TrackFeatures, Transition


class BridgeError(ValueError):
    pass


class BridgeService:
    def __init__(self, repo: Repo, embed_text: Callable[[str], np.ndarray] | None = None) -> None:
        self.repo = repo
        self.embed_text = embed_text
        self._cat: Catalog | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ catalog

    @property
    def catalog(self) -> Catalog:
        with self._lock:
            if self._cat is None:
                tracks = self.repo.all_tracks()
                if len(tracks) < 3:
                    raise BridgeError("catalog has fewer than 3 tracks")
                self._cat = Catalog(tracks)
            return self._cat

    def invalidate(self) -> None:
        with self._lock:
            self._cat = None

    def submit(self, submitter: str, docs: list[TrackFeatures]) -> list[str]:
        ids = self.repo.submit(submitter, docs)
        self.invalidate()
        return ids

    # ------------------------------------------------------------------ queries

    def search(self, q: str, limit: int = 10) -> list[dict]:
        cat = self.catalog
        terms = q.lower().split()
        scored = []
        for i, t in enumerate(cat.tracks):
            hay = f"{t.title} {t.artist}".lower()
            if all(term in hay for term in terms):
                starts = hay.startswith(terms[0]) if terms else False
                scored.append((not starts, -(cat.playcount[i] or 0), i))
        scored.sort()
        return [self._summary(cat, i) for *_, i in scored[:limit]]

    def track(self, track_id: str) -> TrackFeatures:
        cat = self.catalog
        if track_id not in cat.index:
            raise KeyError(track_id)
        return cat.tracks[cat.index[track_id]]

    def _summary(self, cat: Catalog, i: int) -> dict:
        t = cat.tracks[i]
        return {
            "id": t.id,
            "title": t.title,
            "artist": t.artist,
            "bpm": t.global_.bpm,
            "camelot": t.global_.camelot,
            "year": t.year,
            "tier": t.tier,
            "tags": list(t.global_.tags)[:3],
        }

    def _allowed(self, cat: Catalog, req: BridgeRequest) -> np.ndarray:
        f = req.filters
        ok = np.isin(np.array(cat.tier), f.tiers)
        if f.min_playcount:
            known = cat.playcount > 0
            ok &= ~known | (cat.playcount >= f.min_playcount)  # unknown popularity stays eligible
        if not f.allow_explicit:
            ok &= ~cat.explicit
        if f.year_min:
            ok &= (cat.year < 0) | (cat.year >= f.year_min)
        if f.year_max:
            ok &= (cat.year < 0) | (cat.year <= f.year_max)
        return ok

    def bridge(self, req: BridgeRequest) -> BridgeResponse:
        cat = self.catalog
        missing = [w for w in req.waypoints if w not in cat.index]
        if missing:
            raise BridgeError(f"unknown waypoint ids: {missing}")
        wps = [cat.index[w] for w in req.waypoints]
        if len(set(wps)) != len(wps):
            raise BridgeError("waypoints must be distinct")
        gaps = req.gap_lengths or allocate_gaps(cat, wps, req.length)

        steer = None
        if req.prompt and self.embed_text is not None:
            steer = self.embed_text(req.prompt)

        pf = Pathfinder(cat, weights=req.weights, max_per_artist=req.filters.max_per_artist)
        try:
            leg = pf.bridge(wps, gaps, allowed=self._allowed(cat, req), arc=req.arc, steer=steer)
        except ValueError as e:
            raise BridgeError(str(e)) from e

        wp_set = set(wps)
        tracks = []
        for i in leg.path:
            t = cat.tracks[i]
            tracks.append(
                BridgeTrack(
                    id=t.id,
                    title=t.title,
                    artist=t.artist,
                    role="waypoint" if i in wp_set else "bridge",
                    bpm=t.global_.bpm,
                    camelot=t.global_.camelot,
                    energy=round(float(norm_lufs(t.global_.lufs)), 3),
                    valence=t.global_.valence,
                    tier=t.tier,
                    preview_url=t.external_ids.get("deezer_preview"),
                )
            )
        transitions = [
            Transition(from_id=cat.ids[x["from"]], to_id=cat.ids[x["to"]], cost=x["cost"], terms=x["terms"])
            for x in leg.transitions
        ]
        return BridgeResponse(
            tracks=tracks,
            transitions=transitions,
            total_cost=round(leg.cost, 4),
            confidence=round(float(np.mean([cat.full_audio[i] for i in leg.path])), 3),
            weights=pf.w,
        )
