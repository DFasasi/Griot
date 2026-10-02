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
from griot_core.schema import (
    BridgeRequest,
    BridgeResponse,
    BridgeTrack,
    ImportItem,
    Resolution,
    TrackFeatures,
    Transition,
)
from griot_pipelines.resolve import Resolver
from griot_pipelines.sources import norm

DURATION_MATCH_S = 5.0


class BridgeError(ValueError):
    pass


class BridgeService:
    def __init__(
        self,
        repo: Repo,
        embed_text: Callable[[str], np.ndarray] | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.repo = repo
        self.embed_text = embed_text
        self._cat: Catalog | None = None
        self._idx: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.resolver = resolver or Resolver(lookup=self.lookup)

    # ------------------------------------------------------------------ catalog

    @property
    def catalog(self) -> Catalog:
        with self._lock:
            if self._cat is None:
                tracks = self.repo.all_tracks()
                if len(tracks) < 3:
                    raise BridgeError("catalog has fewer than 3 tracks")
                self._cat = Catalog(tracks)
                self._idx = self._build_index(self._cat.tracks)
            return self._cat

    @staticmethod
    def _build_index(tracks: list[TrackFeatures]) -> dict[str, dict]:
        idx: dict[str, dict] = {"isrc": {}, "mbid": {}, "deezer": {}, "name": {}}
        for t in tracks:
            hit = (t.id, t.tier)
            if t.isrc:
                idx["isrc"][t.isrc.upper()] = hit
            if t.mbid:
                idx["mbid"][t.mbid] = hit
            if dz := t.external_ids.get("deezer"):
                idx["deezer"][dz] = hit
            idx["name"].setdefault((norm(t.artist), norm(t.title)), []).append((t.duration_s, hit))
        return idx

    def lookup(
        self,
        isrc: str | None = None,
        deezer_id: str | None = None,
        artist: str | None = None,
        title: str | None = None,
        duration: float | None = None,
        mbid: str | None = None,
    ) -> tuple[str, str] | None:
        """Find a recording already in the catalog by any identity we have for it."""
        _ = self.catalog
        idx = self._idx
        if mbid and mbid in idx["mbid"]:
            return idx["mbid"][mbid]
        if isrc and isrc.upper() in idx["isrc"]:
            return idx["isrc"][isrc.upper()]
        if deezer_id and deezer_id in idx["deezer"]:
            return idx["deezer"][deezer_id]
        for dur, hit in idx["name"].get((norm(artist), norm(title)), []):
            if duration is None or abs(dur - duration) <= DURATION_MATCH_S:
                return hit
        return None

    def invalidate(self) -> None:
        with self._lock:
            self._cat = None

    def submit(self, submitter: str, docs: list[TrackFeatures]) -> list[str]:
        """Store analyses under the catalog's existing id for the same recording, so a file
        analysed locally (MBID id) and a preview analysis (ISRC id) land on one entry."""
        try:
            space = self.catalog.space
        except BridgeError:
            space = None
        bad = [d.id for d in docs if space and (d.embeddings.model, len(d.embeddings.full)) != space]
        if bad:
            raise BridgeError(f"embedding space mismatch (catalog uses {space[0]}, {space[1]}-d): {bad[:5]}")
        for d in docs:
            try:
                hit = self.lookup(
                    mbid=d.mbid,
                    isrc=d.isrc,
                    deezer_id=d.external_ids.get("deezer"),
                    artist=d.artist,
                    title=d.title,
                    duration=d.duration_s,
                )
            except BridgeError:  # catalog still too small to have a match
                hit = None
            if hit and hit[0] != d.id:
                d.id = hit[0]
        ids = self.repo.submit(submitter, docs)
        done = [x for d in docs for x in (d.isrc, f"dz:{d.external_ids.get('deezer')}") if x]
        self.repo.unwant([k for k in done if k and not k.endswith(":None")])
        self.invalidate()
        return ids

    def resolve(self, items: list[ImportItem]) -> list[Resolution]:
        out = [self.resolver.resolve(it) for it in items]
        want = [
            {
                "key": r.isrc or f"dz:{r.deezer_id}",
                "isrc": r.isrc,
                "deezer_id": r.deezer_id,
                "title": r.title,
                "artist": r.artist,
                "duration_s": r.item.duration_s,
                "preview_url": r.preview_url,
            }
            for r in out
            if r.status == "missing"
        ]
        if want:
            self.repo.want(want)
        return out

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
