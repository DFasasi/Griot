"""Storage backends. `PgRepo` is the real one; `MemoryRepo` serves tests, demos and dev
without a database (optionally seeded from a JSONL export or the synthetic catalog)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from griot_api.merge import carry_enrichment, merge
from griot_core.schema import TrackFeatures


def identity_row(t: TrackFeatures) -> dict:
    """The few fields needed to identify, search and validate a recording — no features."""
    p = t.popularity
    return {
        "id": t.id,
        "tier": t.tier,
        "isrc": t.isrc,
        "mbid": t.mbid,
        "deezer": t.external_ids.get("deezer"),
        "artist": t.artist,
        "title": t.title,
        "duration_s": t.duration_s,
        "bpm": t.global_.bpm,
        "camelot": t.global_.camelot,
        "year": t.year,
        "playcount": p.lastfm_playcount or p.lb_listens or 0,
        "tags": list(t.global_.tags)[:3],
        "space": (t.embeddings.model, len(t.embeddings.full)),
    }


class Repo(Protocol):
    def all_tracks(self) -> list[TrackFeatures]: ...
    def identities(self) -> list[dict]: ...
    def get(self, track_id: str) -> TrackFeatures | None: ...
    def submit(self, submitter: str, docs: list[TrackFeatures]) -> list[str]: ...
    def save_bridge(self, request: dict, response: dict) -> str: ...
    def get_bridge(self, bridge_id: str) -> dict | None: ...
    def feedback(self, bridge_id: str, position: int, rating: int) -> None: ...
    def want(self, items: list[dict]) -> None: ...
    def wanted(self, limit: int) -> list[dict]: ...
    def unwant(self, keys: list[str]) -> None: ...
    def library_create(self, lib_id: str) -> None: ...
    def library_get(self, lib_id: str) -> dict[str, dict] | None: ...
    def library_put(self, lib_id: str, entries: dict[str, dict]) -> None: ...


class MemoryRepo:
    def __init__(self, tracks: list[TrackFeatures] | None = None) -> None:
        self.subs: dict[str, dict[str, TrackFeatures]] = defaultdict(dict)
        self.tracks: dict[str, TrackFeatures] = {t.id: t for t in tracks or []}
        self.bridges: dict[str, dict] = {}
        self.ratings: list[tuple[str, int, int]] = []
        self._wanted: dict[str, dict] = {}
        self._libraries: dict[str, dict[str, dict]] = {}

    @classmethod
    def from_jsonl(cls, path: Path) -> MemoryRepo:
        lines = [x for x in path.read_text().splitlines() if x.strip()]
        return cls([TrackFeatures.model_validate_json(x) for x in lines])

    def all_tracks(self) -> list[TrackFeatures]:
        return list(self.tracks.values())

    def identities(self) -> list[dict]:
        return [identity_row(t) for t in self.tracks.values()]

    def get(self, track_id: str) -> TrackFeatures | None:
        return self.tracks.get(track_id)

    def submit(self, submitter: str, docs: list[TrackFeatures]) -> list[str]:
        for d in docs:
            self.subs[d.id][submitter] = d
            self.tracks[d.id] = carry_enrichment(merge(list(self.subs[d.id].values())), self.tracks.get(d.id))
        return [d.id for d in docs]

    def save_bridge(self, request: dict, response: dict) -> str:
        bid = str(uuid.uuid4())
        self.bridges[bid] = {"request": request, "response": response}
        return bid

    def get_bridge(self, bridge_id: str) -> dict | None:
        return self.bridges.get(bridge_id)

    def feedback(self, bridge_id: str, position: int, rating: int) -> None:
        if bridge_id not in self.bridges:
            raise KeyError(bridge_id)
        self.ratings.append((bridge_id, position, rating))

    def want(self, items: list[dict]) -> None:
        for it in items:
            if it["key"] in self._wanted:
                self._wanted[it["key"]]["requests"] += 1
            else:
                self._wanted[it["key"]] = it | {"requests": 1}

    def wanted(self, limit: int) -> list[dict]:
        return sorted(self._wanted.values(), key=lambda x: -x["requests"])[:limit]

    def unwant(self, keys: list[str]) -> None:
        for k in keys:
            self._wanted.pop(k, None)

    def library_create(self, lib_id: str) -> None:
        self._libraries.setdefault(lib_id, {})

    def library_get(self, lib_id: str) -> dict[str, dict] | None:
        return self._libraries.get(lib_id)

    def library_put(self, lib_id: str, entries: dict[str, dict]) -> None:
        self._libraries[lib_id] = entries


def _vec(v: list[float] | None) -> str | None:
    return None if v is None else "[" + ",".join(f"{x:.6f}" for x in v) + "]"


class PgRepo:
    EMB_DIM, LYR_DIM = 512, 384

    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(dsn, min_size=1, max_size=8, open=True)

    def all_tracks(self) -> list[TrackFeatures]:
        with self.pool.connection() as c:
            rows = c.execute("SELECT doc FROM recordings").fetchall()
        return [TrackFeatures.model_validate(r[0]) for r in rows]

    def identities(self) -> list[dict]:
        sql = """
            SELECT id, tier, isrc, mbid::text, external_ids ->> 'deezer', artist, title, duration_s,
                   bpm, camelot,
                   year, coalesce(lastfm_playcount, lb_listens, 0),
                   coalesce((SELECT array_agg(k) FROM (SELECT jsonb_object_keys(doc -> 'global' -> 'tags') k
                                                        LIMIT 3) t), '{}'),
                   doc -> 'embeddings' ->> 'model', jsonb_array_length(doc -> 'embeddings' -> 'full')
            FROM recordings"""
        keys = ("id", "tier", "isrc", "mbid", "deezer", "artist", "title", "duration_s", "bpm", "camelot",
                "year", "playcount", "tags", "model", "dim")  # fmt: skip
        with self.pool.connection() as c:
            rows = [dict(zip(keys, r, strict=True)) for r in c.execute(sql).fetchall()]
        for r in rows:
            r["space"] = (r.pop("model"), r.pop("dim"))
        return rows

    def get(self, track_id: str) -> TrackFeatures | None:
        with self.pool.connection() as c:
            row = c.execute("SELECT doc FROM recordings WHERE id = %s", (track_id,)).fetchone()
        return None if row is None else TrackFeatures.model_validate(row[0])

    def submit(self, submitter: str, docs: list[TrackFeatures]) -> list[str]:
        from psycopg.types.json import Jsonb

        with self.pool.connection() as c, c.transaction():
            for d in docs:
                c.execute(
                    "INSERT INTO submissions (recording_id, submitter, doc) VALUES (%s, %s, %s) "
                    "ON CONFLICT (recording_id, submitter) "
                    "DO UPDATE SET doc = excluded.doc, created_at = now()",
                    (d.id, submitter, Jsonb(d.model_dump(mode="json", by_alias=True))),
                )
                all_docs = [
                    TrackFeatures.model_validate(r[0])
                    for r in c.execute("SELECT doc FROM submissions WHERE recording_id = %s", (d.id,))
                ]
                prev = c.execute("SELECT doc FROM recordings WHERE id = %s", (d.id,)).fetchone()
                old = TrackFeatures.model_validate(prev[0]) if prev else None
                self._upsert(c, carry_enrichment(merge(all_docs), old), len(all_docs))
        return [d.id for d in docs]

    def _upsert(self, c, t: TrackFeatures, n_subs: int) -> None:
        from psycopg.types.json import Jsonb

        e, g, p = t.embeddings, t.global_, t.popularity
        lyr_ok = t.lyrics is not None and t.lyrics.open and len(t.lyrics.open) == self.LYR_DIM
        emb_ok = len(e.full) == self.EMB_DIM
        cols = {
            "id": t.id, "mbid": t.mbid, "isrc": t.isrc, "title": t.title, "artist": t.artist,
            "artist_ids": t.artist_ids, "duration_s": t.duration_s, "year": t.year,
            "explicit": t.explicit, "tier": t.tier, "full_audio": t.analyzer.full_audio,
            "analyzer_version": t.analyzer.version, "bpm": g.bpm, "camelot": g.camelot,
            "lufs": g.lufs, "valence": g.valence, "arousal": g.arousal,
            "lastfm_playcount": p.lastfm_playcount, "lb_listens": p.lb_listens,
            "deezer_rank": p.deezer_rank, "preview_url": t.external_ids.get("deezer_preview"),
            "external_ids": Jsonb(t.external_ids),
            "emb_full": _vec(e.full) if emb_ok else None,
            "emb_intro": _vec(e.intro) if emb_ok else None,
            "emb_outro": _vec(e.outro) if emb_ok else None,
            "lyr_open": _vec(t.lyrics.open) if lyr_ok else None,
            "lyr_close": _vec(t.lyrics.close) if lyr_ok else None,
            "doc": Jsonb(t.model_dump(mode="json", by_alias=True)),
            "n_submissions": n_subs,
        }  # fmt: skip
        names = ", ".join(cols)
        ph = ", ".join(f"%({k})s" for k in cols)
        upd = ", ".join(f"{k} = excluded.{k}" for k in cols if k != "id")
        c.execute(
            f"INSERT INTO recordings ({names}) VALUES ({ph}) "
            f"ON CONFLICT (id) DO UPDATE SET {upd}, updated_at = now()",
            cols,
        )

    def save_bridge(self, request: dict, response: dict) -> str:
        from psycopg.types.json import Jsonb

        with self.pool.connection() as c:
            row = c.execute(
                "INSERT INTO bridges (request, response) VALUES (%s, %s) RETURNING id",
                (Jsonb(request), Jsonb(response)),
            ).fetchone()
        return str(row[0])

    def get_bridge(self, bridge_id: str) -> dict | None:
        with self.pool.connection() as c:
            row = c.execute("SELECT request, response FROM bridges WHERE id = %s", (bridge_id,)).fetchone()
        return None if row is None else {"request": row[0], "response": row[1]}

    def feedback(self, bridge_id: str, position: int, rating: int) -> None:
        with self.pool.connection() as c:
            c.execute(
                "INSERT INTO bridge_feedback (bridge_id, position, rating) VALUES (%s, %s, %s)",
                (bridge_id, position, rating),
            )

    def want(self, items: list[dict]) -> None:
        with self.pool.connection() as c, c.transaction():
            for it in items:
                c.execute(
                    "INSERT INTO wanted (key, isrc, deezer_id, title, artist, duration_s, preview_url) "
                    "VALUES (%(key)s, %(isrc)s, %(deezer_id)s, %(title)s, %(artist)s, %(duration_s)s, "
                    "%(preview_url)s) ON CONFLICT (key) DO UPDATE SET requests = wanted.requests + 1, "
                    "preview_url = coalesce(excluded.preview_url, wanted.preview_url), updated_at = now()",
                    it,
                )

    def wanted(self, limit: int) -> list[dict]:
        with self.pool.connection() as c:
            cur = c.execute(
                "SELECT key, isrc, deezer_id, title, artist, duration_s, preview_url, requests "
                "FROM wanted ORDER BY requests DESC, created_at LIMIT %s",
                (limit,),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def unwant(self, keys: list[str]) -> None:
        with self.pool.connection() as c:
            c.execute("DELETE FROM wanted WHERE key = ANY(%s)", (keys,))

    def library_create(self, lib_id: str) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO libraries (id) VALUES (%s) ON CONFLICT DO NOTHING", (lib_id,))

    def library_get(self, lib_id: str) -> dict[str, dict] | None:
        with self.pool.connection() as c:
            row = c.execute("SELECT entries FROM libraries WHERE id = %s", (lib_id,)).fetchone()
        return None if row is None else row[0]

    def library_put(self, lib_id: str, entries: dict[str, dict]) -> None:
        from psycopg.types.json import Jsonb

        with self.pool.connection() as c:
            c.execute(
                "UPDATE libraries SET entries = %s, updated_at = now() WHERE id = %s",
                (Jsonb(entries), lib_id),
            )
