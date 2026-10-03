"""Bridge service: keeps an in-memory Catalog over the repo and answers bridge/search queries.

At MVP scale (≤ a few hundred thousand tracks) brute-force numpy over an in-memory catalog
is faster than a round trip to pgvector; the HNSW indexes take over once the catalog outgrows RAM.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable

import numpy as np

from griot_api.repo import Repo, identity_row
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
        self._cat_built = 0.0
        self._stale = False
        self._rebuilding = False
        self._rows: dict[str, dict] | None = None  # light identity rows by id
        self._idx: dict[str, dict] = {}
        self._spaces: Counter = Counter()
        self._lock = threading.Lock()
        self._ilock = threading.Lock()
        self.resolver = resolver or Resolver(lookup=self.lookup)

    # ------------------------------------------------------------------ identity index
    # Identifying songs (import matching, submissions, search) only needs ids and names, so it
    # runs on a light index that is updated in place — never on the full feature catalog.

    def _ensure_index(self) -> dict[str, dict]:
        with self._ilock:
            if self._rows is None:
                self._rows, self._idx, self._spaces = (
                    {},
                    {"isrc": {}, "mbid": {}, "deezer": {}, "name": {}},
                    Counter(),
                )
                for r in self.repo.identities():
                    self._index_add(r)
            return self._rows

    def _index_add(self, r: dict) -> None:
        old = self._rows.get(r["id"])
        if old is not None:
            self._spaces[old["space"]] -= 1
        self._rows[r["id"]] = r
        self._spaces[r["space"]] += 1
        hit = (r["id"], r["tier"])
        if r.get("isrc"):
            self._idx["isrc"][r["isrc"].upper()] = hit
        if r.get("mbid"):
            self._idx["mbid"][r["mbid"]] = hit
        if r.get("deezer"):
            self._idx["deezer"][r["deezer"]] = hit
        names = self._idx["name"].setdefault((norm(r["artist"]), norm(r["title"])), [])
        names[:] = [n for n in names if n[1][0] != r["id"]] + [(r["duration_s"], hit)]

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
        self._ensure_index()
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

    def track_count(self) -> int:
        return len(self._ensure_index())

    # ------------------------------------------------------------------ feature catalog (bridges)

    CATALOG_REFRESH_S = 30.0

    @property
    def catalog(self) -> Catalog:
        """Full features for pathfinding. Built synchronously only the first time; after new
        songs arrive it is refreshed in the background (at most every 30 s) while bridges keep
        using the current one — so ingestion never stalls a bridge request."""
        with self._lock:
            if self._cat is None:
                self._cat = self._build_catalog()
                self._cat_built, self._stale = time.time(), False
            elif (
                self._stale
                and not self._rebuilding
                and time.time() - self._cat_built >= self.CATALOG_REFRESH_S
            ):
                self._rebuilding = True
                threading.Thread(target=self._rebuild_in_background, daemon=True).start()
            return self._cat

    def _build_catalog(self) -> Catalog:
        tracks = self.repo.all_tracks()
        if len(tracks) < 3:
            raise BridgeError("catalog has fewer than 3 tracks")
        return Catalog(tracks)

    def _rebuild_in_background(self) -> None:
        try:
            fresh = self._build_catalog()
            with self._lock:
                self._cat, self._cat_built, self._stale = fresh, time.time(), False
        except Exception:
            pass  # keep serving the previous catalog; the next request retries
        finally:
            self._rebuilding = False

    def invalidate(self) -> None:
        with self._lock:
            self._cat, self._stale = None, False
        with self._ilock:
            self._rows = None

    def submit(self, submitter: str, docs: list[TrackFeatures]) -> list[str]:
        """Store analyses under the catalog's existing id for the same recording, so a file
        analysed locally (MBID id) and a preview analysis (ISRC id) land on one entry."""
        rows = self._ensure_index()
        space = self._spaces.most_common(1)[0][0] if rows and self._spaces else None
        if space and len(rows) >= 3:
            bad = [d.id for d in docs if (d.embeddings.model, len(d.embeddings.full)) != space]
            if bad:
                raise BridgeError(
                    f"embedding space mismatch (catalog uses {space[0]}, {space[1]}-d): {bad[:5]}"
                )
        for d in docs:
            hit = self.lookup(
                mbid=d.mbid,
                isrc=d.isrc,
                deezer_id=d.external_ids.get("deezer"),
                artist=d.artist,
                title=d.title,
                duration=d.duration_s,
            )
            if hit and hit[0] != d.id:
                d.id = hit[0]
        ids = self.repo.submit(submitter, docs)
        with self._ilock:
            for d in docs:
                self._index_add(identity_row(d))
        done = [x for d in docs for x in (d.isrc, f"dz:{d.external_ids.get('deezer')}") if x]
        self.repo.unwant([k for k in done if k and not k.endswith(":None")])
        with self._lock:
            self._stale = True
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
        rows = self._ensure_index()
        terms = q.lower().split()
        scored = []
        for r in rows.values():
            hay = f"{r['title']} {r['artist']}".lower()
            if all(term in hay for term in terms):
                starts = hay.startswith(terms[0]) if terms else False
                scored.append((not starts, -(r["playcount"] or 0), r["id"]))
        scored.sort()
        keys = ("id", "title", "artist", "bpm", "camelot", "year", "tier", "tags")
        return [{k: rows[i][k] for k in keys} for *_, i in scored[:limit]]

    def track(self, track_id: str) -> TrackFeatures:
        t = self.repo.get(track_id)
        if t is None:
            raise KeyError(track_id)
        return t

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

    ISOLATED_SEAM = 0.6  # best available seam worse than this -> the catalog is thin around a waypoint

    @staticmethod
    def _isolation_notes(cat: Catalog, wps: list[int], allowed: np.ndarray, w: dict) -> list[str]:
        """Warn when even the smoothest possible transition out of/into a waypoint is rough, so a
        jarring step is explained by the catalog rather than looking like a bad choice."""
        from griot_core.cost import transition_cost as tc

        mask = allowed.copy()
        mask[wps] = False  # a song flows perfectly into itself; only other songs count
        pool = np.flatnonzero(mask)
        notes = []
        for k, i in enumerate(wps):
            t = cat.tracks[i]
            best_out = float(tc(cat, [i], pool, w)[0].min()) if k < len(wps) - 1 else 0.0
            best_in = float(tc(cat, pool, [i], w)[:, 0].min()) if k > 0 else 0.0
            if max(best_out, best_in) > BridgeService.ISOLATED_SEAM:
                notes.append(
                    f"Few songs in the catalog flow smoothly {'out of' if best_out >= best_in else 'into'} "
                    f"“{t.title}” by {t.artist}, so the first step near it is a bigger jump. "
                    "Import or analyse more songs like it to smooth this out."
                )
        return notes

    def active_weights(self) -> dict[str, float] | None:
        """Weights tuned from listener ratings, if a tuning run has been applied."""
        tuned = self.repo.get_setting("weights")
        return tuned.get("weights") if tuned else None

    def tuning_status(self) -> dict:
        from griot_core.tuning import MIN_EACH, MIN_RATINGS

        rows = self.repo.feedback_rows()
        up = sum(1 for *_, r in rows if r > 0)
        return {
            "ratings": len(rows),
            "up": up,
            "down": len(rows) - up,
            "needed": MIN_RATINGS,
            "min_each": MIN_EACH,
            "ready": len(rows) >= MIN_RATINGS and min(up, len(rows) - up) >= MIN_EACH,
            "last": self.repo.get_setting("weights"),
        }

    def run_tuning(self, apply: bool = True) -> dict:
        import datetime as dt

        import numpy as np

        from griot_core.cost import merged_weights
        from griot_core.tuning import features, tune

        rows = [(features(t, w), r) for t, w, r in self.repo.feedback_rows()]
        rows = [(x, r) for x, r in rows if x is not None]
        current = merged_weights(self.active_weights())
        X = np.array([x for x, _ in rows]) if rows else np.zeros((0, 7))
        y = np.array([1 if r > 0 else 0 for _, r in rows])
        rep = tune(X, y, current)
        result = {
            k: getattr(rep, k)
            for k in ("n", "up", "down", "auc_current", "auc_tuned", "current", "tuned", "apply", "reason")
        }
        if rep.apply and apply:
            self.repo.set_setting(
                "weights",
                {
                    "weights": rep.tuned,
                    "at": dt.datetime.now(dt.UTC).isoformat(),
                    "auc": [rep.auc_current, rep.auc_tuned],
                    "n": rep.n,
                },
            )
        return result

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

        pf = Pathfinder(
            cat, weights=req.weights or self.active_weights(), max_per_artist=req.filters.max_per_artist
        )
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
        notes = self._isolation_notes(cat, wps, self._allowed(cat, req), pf.w)
        return BridgeResponse(
            notes=notes,
            tracks=tracks,
            transitions=transitions,
            total_cost=round(leg.cost, 4),
            confidence=round(float(np.mean([cat.full_audio[i] for i in leg.path])), 3),
            weights=pf.w,
        )
