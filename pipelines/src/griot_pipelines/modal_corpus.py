"""Backfill the open, full-length corpus on Modal GPUs (tier "A-open").

Source: Jamendo — Creative Commons tracks with `audiodownload_allowed`, ordered by
popularity. Audio is downloaded inside the container, analyzed with the exact same
`griot_analyzer.extract` code the local analyzer uses, and discarded; only features return.

    modal secret create jamendo JAMENDO_CLIENT_ID=<id>      # free key: devportal.jamendo.com
    modal run pipelines/src/griot_pipelines/modal_corpus.py --n 5000 --out data/corpus/jamendo.jsonl
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
