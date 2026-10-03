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

        import httpx

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
        for q in batch:
            p = work / f"{q['key'].replace(':', '_')}.mp3"
            try:
                p.write_bytes(
                    httpx.get(q["preview_url"], timeout=60, follow_redirects=True).raise_for_status().content
                )
                paths.append(p)
                meta[p] = q
            except httpx.HTTPError:
                continue  # previews expire; skipped songs stay queued
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
                feats = build_features(p, struct, desc, centres, embs, zero_shot(embs, self.models))
                feats["analyzer"].full_audio = False
                feats["duration_s"] = q.get("duration_s") or feats["duration_s"]
                doc = TrackFeatures(
                    id=f"isrc:{q['isrc']}" if q.get("isrc") else f"deezer:{q['deezer_id']}",
                    isrc=q.get("isrc"),
                    title=q["title"],
                    artist=q["artist"],
                    tier="B",
                    external_ids={"deezer": str(q["deezer_id"]), "deezer_preview": q["preview_url"]},
                    **feats,
                )
                docs.append(doc.model_dump(mode="json", by_alias=True))
            except Exception as e:
                failed.append(f"{q['artist']} — {q['title']}: {e}")
            finally:
                p.unlink(missing_ok=True)
        hf_cache.commit()
        return {"docs": docs, "failed": failed, "seconds": time.time() - started, "asked": len(batch)}


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


L4_PER_HOUR = 0.80  # USD, Modal list price; container time also includes model loading


@app.local_entrypoint()
def previews(n: int = 200, batch: int = 10) -> None:
    """Read the local API's queue, analyse previews on Modal, submit results back locally."""
    import os
    import time

    import httpx

    from griot_core.env import load_dotenv

    load_dotenv()
    api = os.environ.get("GRIOT_API_URL", "http://127.0.0.1:8000")
    headers = {"Authorization": f"Bearer {os.environ.get('GRIOT_SUBMIT_TOKEN', '')}"}
    with httpx.Client(base_url=api, headers=headers, timeout=120) as c:
        queue = [
            q for q in c.get("/wanted", params={"limit": n}).raise_for_status().json() if q.get("preview_url")
        ]
        print(f"{len(queue)} queued songs with previews")
        batches = [queue[i : i + batch] for i in range(0, len(queue), batch)]
        started, gpu_s, done, failed = time.time(), 0.0, 0, []
        for res in Analyzer().analyze_previews.map(batches, order_outputs=False):
            gpu_s += res["seconds"]
            failed += res["failed"]
            if res["docs"]:
                c.post("/submissions", json=res["docs"]).raise_for_status()
                done += len(res["docs"])
            print(f"{done}/{len(queue)} analysed · {len(failed)} failed")
        wall = time.time() - started
    per_song = gpu_s / max(done, 1)
    print(
        json.dumps(
            {
                "analysed": done,
                "failed": len(failed),
                "wall_minutes": round(wall / 60, 1),
                "gpu_seconds_per_song": round(per_song, 1),
                "est_cost_usd_this_run": round(gpu_s / 3600 * L4_PER_HOUR, 2),
                "est_cost_usd_per_1000": round(per_song * 1000 / 3600 * L4_PER_HOUR, 2),
                "sample_failures": failed[:5],
            },
            indent=2,
        )
    )
