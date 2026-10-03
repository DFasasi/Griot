"""Analyse audio on Modal GPUs.

`main`     — backfill the open, full-length corpus (tier "A-open") from Jamendo.
`previews` — work through the import queue using 30 s Deezer previews (tier "B").

Source: Jamendo — Creative Commons tracks with `audiodownload_allowed`, ordered by
popularity. Audio is downloaded inside the container, analyzed with the exact same
`griot_analyzer.extract` code the local analyzer uses, and discarded; only features return.

    modal secret create jamendo JAMENDO_CLIENT_ID=<id>      # free key: devportal.jamendo.com
    modal run pipelines/src/griot_pipelines/modal_corpus.py --n 5000 --out data/corpus/jamendo.jsonl
    modal run pipelines/src/griot_pipelines/modal_corpus.py::previews --n 200
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import modal

app = modal.App("griot-corpus")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "git")
    .pip_install(
        "torch>=2.3",
        "librosa>=0.10",
        "soundfile>=0.12",
        "essentia>=2.1b6.dev1110",
        "muq>=0.1",
        "transformers>=4.45,<4.50",
        "all-in-one-infer>=3.1",
        "numpy>=1.26",
        "pydantic>=2.7",
        "httpx>=0.27",
        "mutagen>=1.47",
    )
    .add_local_python_source("griot_core", "griot_analyzer")
)
hf_cache = modal.Volume.from_name("griot-hf-cache", create_if_missing=True)

JAMENDO = "https://api.jamendo.com/v3.0/tracks/"


@app.function(image=image, secrets=[modal.Secret.from_name("jamendo")], timeout=1800)
def list_tracks(n: int, min_duration: int = 90) -> list[dict]:
    import httpx

    out, offset = [], 0
    while len(out) < n:
        r = httpx.get(
            JAMENDO,
            params={
                "client_id": os.environ["JAMENDO_CLIENT_ID"],
                "format": "json",
                "limit": 200,
                "offset": offset,
                "order": "popularity_total",
                "include": "musicinfo stats",
                "audiodlformat": "mp32",
                "durationbetween": f"{min_duration}_600",
            },
            timeout=30,
        )
        r.raise_for_status()
        page = r.json().get("results", [])
        if not page:
            break
        out += [t for t in page if t.get("audiodownload_allowed") and t.get("audiodownload")]
        offset += 200
    return out[:n]


@app.cls(
    image=image,
    gpu="L4",
    timeout=3600,
    volumes={"/root/.cache/huggingface": hf_cache},
    max_containers=20,
)
class Analyzer:
    @modal.enter()
    def load(self) -> None:
        from griot_analyzer.extract import Models

        self.models = Models()
        _ = self.models.mulan, self.models.text_anchors  # warm weights once per container

    @modal.method()
    def analyze(self, batch: list[dict]) -> list[str]:
        import tempfile

        import httpx

        from griot_analyzer.extract import (
            analyze_descriptors,
            analyze_structure,
            build_features,
            window_embeddings,
            zero_shot,
        )
        from griot_core.schema import TrackFeatures

        work = Path(tempfile.mkdtemp())
        paths, meta = [], {}
        for t in batch:
            p = work / f"{t['id']}.mp3"
            try:
                p.write_bytes(httpx.get(t["audiodownload"], timeout=120, follow_redirects=True).content)
                paths.append(p)
                meta[p] = t
            except httpx.HTTPError:
                continue
        try:
            structs = analyze_structure(paths, work)
        except Exception:
            structs = {}

        docs = []
        for p in paths:
            t = meta[p]
            try:
                struct = structs.get(p) or analyze_structure([p], work)[p]
                desc = analyze_descriptors(p)
                centres, embs = window_embeddings(p, self.models)
                feats = build_features(p, struct, desc, centres, embs, zero_shot(embs, self.models))
                year = (t.get("releasedate") or "")[:4]
                tf = TrackFeatures(
                    id=f"jamendo:{t['id']}",
                    title=t["name"],
                    artist=t["artist_name"],
                    artist_ids=[f"jamendo-artist:{t['artist_id']}"],
                    year=int(year) if year.isdigit() else None,
                    tier="A-open",
                    external_ids={
                        "jamendo": str(t["id"]),
                        "jamendo_listens": str((t.get("stats") or {}).get("listened_all", "")),
                        "license": t.get("license_ccurl", ""),
                    },
                    **feats,
                )
                docs.append(tf.model_dump_json(by_alias=True))
            except Exception as e:
                print(f"failed jamendo:{t['id']}: {e}")
            finally:
                p.unlink(missing_ok=True)
        hf_cache.commit()
        return docs

    @modal.method()
    def analyze_previews(self, batch: list[dict]) -> dict:
        """Queue items (title, artist, isrc, deezer_id, preview_url) -> tier-B TrackFeatures."""
        import tempfile
        import time

        from griot_analyzer.extract import (
            analyze_descriptors,
            analyze_structure,
            build_features,
            window_embeddings,
            zero_shot,
        )
        from griot_core.schema import TrackFeatures

        started = time.time()
        work = Path(tempfile.mkdtemp())
        paths, meta = [], {}
        skipped = []
        for q in batch:
            p = work / f"{q['key'].replace(':', '_')}.mp3"
            try:
                p.write_bytes(_download_preview(q))
                paths.append(p)
                meta[p] = q
            except Exception as e:  # no preview available any more: stays queued
                skipped.append(f"{q['artist']} — {q['title']}: {e}")
        try:
            structs = analyze_structure(paths, work)
        except Exception:
            structs = {}
        docs, failed = [], []
        for p in paths:
            q = meta[p]
            try:
                struct = structs.get(p) or analyze_structure([p], work)[p]
                desc = analyze_descriptors(p)
                centres, embs = window_embeddings(p, self.models)
                feats = build_features(
                    p, struct, desc, centres, embs, zero_shot(embs, self.models), bpm_hint=q.get("deezer_bpm")
                )
                feats["analyzer"].full_audio = False
                feats["duration_s"] = q.get("duration_s") or feats["duration_s"]
                doc = TrackFeatures(
                    id=f"isrc:{q['isrc']}" if q.get("isrc") else f"deezer:{q['deezer_id']}",
                    isrc=q.get("isrc"),
                    title=q["title"],
                    artist=q["artist"],
                    tier="B",
                    external_ids={
                        "deezer": str(q["deezer_id"]),
                        "deezer_preview": q.get("fresh_preview", ""),
                    },
                    **feats,
                )
                docs.append(doc.model_dump(mode="json", by_alias=True))
            except Exception as e:
                failed.append(f"{q['artist']} — {q['title']}: {e}")
            finally:
                p.unlink(missing_ok=True)
        hf_cache.commit()
        return {
            "docs": docs,
            "failed": failed,
            "skipped": skipped,
            "seconds": time.time() - started,
            "asked": len(batch),
        }


@app.local_entrypoint()
def main(n: int = 1000, batch: int = 16, out: str = "data/corpus/jamendo.jsonl") -> None:
    tracks = list_tracks.remote(n)
    print(f"{len(tracks)} downloadable tracks listed")
    batches = [tracks[i : i + batch] for i in range(0, len(tracks), batch)]
    dst = Path(out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with dst.open("a") as f:
        for docs in Analyzer().analyze.map(batches, order_outputs=False):
            for d in docs:
                f.write(d + "\n")
            done += len(docs)
            print(f"{done}/{len(tracks)} analyzed")
    print(json.dumps({"written": done, "out": str(dst)}))


def _download_preview(q: dict) -> bytes:
    """Deezer preview links are signed and expire after a few hours; when the stored one has
    lapsed, ask Deezer for a fresh link by track id."""
    import httpx

    r = httpx.get(q["preview_url"], timeout=60, follow_redirects=True) if q.get("preview_url") else None
    if r is not None and r.status_code == 200 and r.content:
        q["fresh_preview"] = q["preview_url"]
        return r.content
    import random
    import time

    for attempt in range(6):
        track = httpx.get(f"https://api.deezer.com/track/{q['deezer_id']}", timeout=30).json()
        # Many GPU containers share egress; Deezer answers its rate limit with HTTP 200 + code 4.
        if (track.get("error") or {}).get("code") == 4:
            time.sleep(1.5 * 2**attempt * (0.5 + random.random()))
            continue
        break
    else:
        raise RuntimeError("Deezer rate limit: try again later")
    url = track.get("preview")
    if not url:
        raise RuntimeError("Deezer has no preview for this track")
    q["fresh_preview"] = url
    return httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().content


def _fresh_preview(deezer, deezer_id: str) -> tuple[str | None, str | float]:
    """-> (url, deezer_bpm) or (None, reason). Deezer answers rate limits with HTTP 200 + code 4."""
    import random
    import time

    from griot_pipelines.sources import SourceUnavailable

    for attempt in range(6):
        try:
            t = deezer._get(f"/track/{deezer_id}")
        except SourceUnavailable:
            return None, "Deezer unreachable"
        err = (t or {}).get("error") or {}
        if err.get("code") == 4:
            time.sleep(1.5 * 2**attempt * (0.5 + random.random()))
            continue
        if err or not t:
            return None, "track not on Deezer any more"
        return (t["preview"], t.get("bpm") or 0.0) if t.get("preview") else (None, "Deezer has no preview")
    return None, "Deezer rate limit"


L4_PER_HOUR = 0.80  # USD, Modal list price; container time also includes model loading


@app.local_entrypoint()
def previews(n: int = 200, batch: int = 10) -> None:
    """Work through the local API's queue on Modal, `n` songs at most (a cost guard).

    The queue endpoint returns ≤1000 songs per call, so this runs in passes, never retrying a
    song already attempted in this run (songs without a preview stay queued for next time).
    """
    import os
    import time
    from collections import Counter

    import httpx

    from griot_core.env import load_dotenv
    from griot_pipelines.sources import Deezer

    load_dotenv()
    api = os.environ.get("GRIOT_API_URL", "http://127.0.0.1:8000")
    headers = {"Authorization": f"Bearer {os.environ.get('GRIOT_SUBMIT_TOKEN', '')}"}
    spool_dir = Path("data/spool")
    spool_dir.mkdir(parents=True, exist_ok=True)
    reasons: Counter = Counter()

    def submit(c: httpx.Client, docs: list[dict]) -> bool:
        """Submit with retries; if the API stays unreachable, keep the results on disk."""
        for attempt in range(4):
            try:
                c.post("/submissions", json=docs).raise_for_status()
                return True
            except httpx.HTTPError:
                time.sleep(2 * 2**attempt)
        with (spool_dir / f"previews-{int(time.time())}.jsonl").open("a") as f:
            f.writelines(json.dumps(d) + "\n" for d in docs)
        reasons["spooled (API unreachable; resent next run)"] += len(docs)
        return False

    with httpx.Client(base_url=api, headers=headers, timeout=300) as c:
        for spool in sorted(spool_dir.glob("previews-*.jsonl")):  # results a previous run couldn't deliver
            docs = [json.loads(x) for x in spool.read_text().splitlines() if x.strip()]
            if docs and all(submit(c, docs[i : i + 25]) for i in range(0, len(docs), 25)):
                spool.unlink()
                print(f"resent {len(docs)} spooled results")

        deezer = Deezer()
        started, gpu_s, done, failed = time.time(), 0.0, 0, []
        attempted: set[str] = set()
        while len(attempted) < n:
            page = c.get("/wanted", params={"limit": 1000}).raise_for_status().json()
            queue = [q for q in page if q.get("deezer_id") and q["key"] not in attempted][
                : n - len(attempted)
            ]
            if not queue:
                break
            attempted.update(q["key"] for q in queue)
            # Fresh preview links from this machine, at a polite rate: GPU containers then only
            # download audio and never hit Deezer's API (which throttles parallel callers).
            ready = []
            for q in queue:
                url, extra = _fresh_preview(deezer, q["deezer_id"])
                if url:
                    ready.append(q | {"preview_url": url, "deezer_bpm": extra})
                else:
                    reasons[extra] += 1
            print(
                f"pass: {len(queue)} songs, {len(ready)} with fresh previews (attempted {len(attempted)}/{n})"
            )
            batches = [ready[i : i + batch] for i in range(0, len(ready), batch)]
            for res in Analyzer().analyze_previews.map(batches, order_outputs=False):
                gpu_s += res["seconds"]
                failed += res["failed"]
                for sk in res["skipped"]:
                    reasons["download failed: " + sk.rsplit(": ", 1)[-1][:60]] += 1
                if res["docs"] and submit(c, res["docs"]):
                    done += len(res["docs"])
                cost = gpu_s / 3600 * L4_PER_HOUR
                print(
                    f"{done} analysed · {len(failed)} failed · {sum(reasons.values())} skipped · ${cost:.2f}"
                )
        wall = time.time() - started
    per_song = gpu_s / max(done, 1)
    print(
        json.dumps(
            {
                "analysed": done,
                "failed": len(failed),
                "skipped": dict(reasons),
                "wall_minutes": round(wall / 60, 1),
                "gpu_seconds_per_song": round(per_song, 1),
                "est_cost_usd_this_run": round(gpu_s / 3600 * L4_PER_HOUR, 2),
                "est_cost_usd_per_1000": round(per_song * 1000 / 3600 * L4_PER_HOUR, 2),
                "sample_failures": failed[:5],
            },
            indent=2,
        )
    )
