"""griot-enrich — add popularity, previews and lyric features to analyzed tracks."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from griot_core.env import load_dotenv
from griot_core.schema import TrackFeatures
from griot_pipelines.enrich import Enricher, EnrichStats

app = typer.Typer(help=__doc__, no_args_is_help=True)
load_dotenv()
console = Console()


def _run(tracks: list[TrackFeatures], lyrics: bool, themes: bool) -> EnrichStats:
    enricher = Enricher(lyrics=lyrics, themes=themes)
    if not os.environ.get("LASTFM_API_KEY"):
        console.print("[yellow]LASTFM_API_KEY not set: popularity from ListenBrainz/Deezer only")
    with Progress(console=console) as prog:
        task = prog.add_task("enriching", total=len(tracks))
        stats = enricher.run(tracks, progress=lambda: prog.advance(task))
    console.print({k: v for k, v in vars(stats).items() if k != "errors"})
    for e in stats.errors[:10]:
        console.print(f"[red]{e}")
    return stats


@app.command()
def jsonl(
    src: Path,
    dst: Path,
    lyrics: bool = typer.Option(True, help="Fetch lyrics and embed them"),
    themes: bool = typer.Option(True, help="Tag lyric themes with Claude"),
) -> None:
    """Enrich an analyzer export (`griot export`) into a new JSONL file."""
    tracks = [TrackFeatures.model_validate_json(x) for x in src.read_text().splitlines() if x.strip()]
    _run(tracks, lyrics, themes)
    dst.write_text("\n".join(t.model_dump_json(by_alias=True) for t in tracks) + "\n")
    console.print(f"wrote {len(tracks)} tracks to {dst}")


@app.command()
def db(
    dsn: str = typer.Option(os.environ.get("DATABASE_URL", ""), help="Postgres DSN"),
    lyrics: bool = True,
    themes: bool = True,
) -> None:
    """Enrich every recording in the Postgres catalog in place."""
    from griot_api.repo import PgRepo

    repo = PgRepo(dsn)
    tracks = repo.all_tracks()
    _run(tracks, lyrics, themes)
    with repo.pool.connection() as c, c.transaction():
        for t in tracks:
            n = c.execute("SELECT n_submissions FROM recordings WHERE id=%s", (t.id,)).fetchone()[0]
            repo._upsert(c, t, n)
    console.print(f"updated {len(tracks)} recordings")


@app.command()
def previews(
    api: str = typer.Option(os.environ.get("GRIOT_API_URL", "http://localhost:8000")),
    token: str = typer.Option(os.environ.get("GRIOT_SUBMIT_TOKEN", ""), help="Submit token for the API"),
    limit: int = typer.Option(50, help="Queue items to process this run"),
) -> None:
    """Analyse 30 s Deezer previews for queued songs nobody has analysed yet (tier B).

    These are stopgaps: they're marked preview-only and replaced as soon as anyone
    submits a full-song analysis of the same recording.
    """
    import tempfile

    import httpx

    from griot_analyzer.extract import (
        Models,
        analyze_descriptors,
        analyze_structure,
        build_features,
        window_embeddings,
        zero_shot,
    )

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=api, headers=headers, timeout=60) as c:
        queue = c.get("/wanted", params={"limit": limit}).raise_for_status().json()
        todo = [q for q in queue if q.get("preview_url")]
        if not todo:
            console.print("queue empty")
            return
        models, work, done = Models(), Path(tempfile.mkdtemp()), 0
        for q in todo:
            path = work / f"{q['key'].replace(':', '_')}.mp3"
            try:
                path.write_bytes(httpx.get(q["preview_url"], timeout=30).raise_for_status().content)
                struct = analyze_structure([path], work)[path]
                desc = analyze_descriptors(path)
                centres, embs = window_embeddings(path, models)
                feats = build_features(path, struct, desc, centres, embs, zero_shot(embs, models))
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
                c.post("/submissions", json=[doc.model_dump(mode="json", by_alias=True)]).raise_for_status()
                done += 1
                console.print(f"[green]preview analysed[/] {q['artist']} — {q['title']}")
            except Exception as e:  # keep draining the queue
                console.print(f"[red]failed[/] {q['artist']} — {q['title']}: {e}")
            finally:
                path.unlink(missing_ok=True)
    console.print(f"{done}/{len(todo)} previews analysed")


def _measure_tempo(job: tuple[str, str, float]) -> tuple[str, float | None, str]:
    """(track id, deezer id, stored bpm) -> (id, corrected bpm or None, note). Runs in a worker."""
    import tempfile

    import httpx

    from griot_analyzer.extract import reconcile_bpm
    from griot_pipelines.sources import Deezer

    tid, dz_id, stored = job
    dz = _measure_tempo.dz = getattr(_measure_tempo, "dz", None) or Deezer()
    dz.limit.interval = 1.0  # 8 workers x 1 req/s stays under Deezer's limit
    try:
        t = dz._get(f"/track/{dz_id}") or {}
        if (t.get("error") or {}).get("code") == 4:
            return tid, None, "rate limited"
        if not t.get("preview"):
            return tid, None, "no preview"
        audio = httpx.get(t["preview"], timeout=60).raise_for_status().content
        with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
            f.write(audio)
            f.flush()
            import essentia

            essentia.log.warningActive = False
            import essentia.standard as es

            measured, *_ = es.RhythmExtractor2013(method="multifeature")(es.MonoLoader(filename=f.name)())
        return tid, round(reconcile_bpm(stored, float(measured), t.get("bpm") or None), 2), "ok"
    except Exception as e:
        return tid, None, f"error: {type(e).__name__}"


@app.command("fix-tempo")
def fix_tempo(
    dsn: str = typer.Option(os.environ.get("DATABASE_URL", ""), help="Postgres DSN (to read the catalog)"),
    api: str = typer.Option(os.environ.get("GRIOT_API_URL", "http://127.0.0.1:8000")),
    token: str = typer.Option(os.environ.get("GRIOT_SUBMIT_TOKEN", ""), help="Submit token for the API"),
    workers: int = typer.Option(8, help="Parallel workers"),
    limit: int = typer.Option(0, help="Only check this many tracks (0 = all)"),
    dry_run: bool = typer.Option(False, help="Report what would change without writing"),
) -> None:
    """Re-measure preview-analysed tempos and correct the wrong ones.

    Early preview runs trusted the song-structure model's tempo, which is unreliable on 30 s
    clips. Each track's preview is re-measured with Essentia and reconciled with the stored
    estimate and Deezer's published BPM (when it has one); corrections are resubmitted through
    the API so the merged catalog stays consistent.
    """
    from collections import Counter
    from multiprocessing import Pool

    import httpx

    from griot_api.repo import PgRepo

    repo = PgRepo(dsn)
    tracks = {t.id: t for t in repo.all_tracks() if t.tier == "B" and t.external_ids.get("deezer")}
    jobs = [(t.id, t.external_ids["deezer"], t.global_.bpm) for t in tracks.values()]
    if limit:
        jobs = jobs[:limit]
    fixed, notes = [], Counter()
    with Progress(console=console) as prog, Pool(workers) as pool:
        task = prog.add_task("re-measuring tempos", total=len(jobs))
        for tid, bpm, note in pool.imap_unordered(_measure_tempo, jobs):
            prog.advance(task)
            notes[note] += 1
            t = tracks[tid]
            if bpm and abs(bpm - t.global_.bpm) / t.global_.bpm > 0.04:
                fixed.append((t, t.global_.bpm, bpm))
    console.print(f"{len(fixed)} tempos corrected of {len(jobs)} checked · {dict(notes)}")
    for t, old, new in fixed[:10]:
        console.print(f"  {t.artist} — {t.title}: {old:.0f} → {new:.0f} BPM")
    if dry_run or not fixed:
        return
    for t, _, new in fixed:
        t.global_.bpm = new
    with httpx.Client(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=300) as c:
        for i in range(0, len(fixed), 25):
            docs = [t.model_dump(mode="json", by_alias=True) for t, _, _ in fixed[i : i + 25]]
            c.post("/submissions", json=docs).raise_for_status()
    console.print(f"resubmitted {len(fixed)} corrected tracks")


@app.command()
def tune(
    api: str = typer.Option(os.environ.get("GRIOT_API_URL", "http://127.0.0.1:8000")),
    token: str = typer.Option(os.environ.get("GRIOT_SUBMIT_TOKEN", ""), help="Submit token for the API"),
    dry_run: bool = typer.Option(False, help="Fit and report, but don't apply"),
) -> None:
    """Tune transition scoring to your 👍/👎 ratings (applied only if it predicts them better)."""
    import httpx

    r = httpx.post(
        f"{api}/tuning/run",
        params={"apply": not dry_run},
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
    )
    r.raise_for_status()
    rep = r.json()
    console.print(f"{rep['n']} rated transitions ({rep['up']} 👍, {rep['down']} 👎)")
    console.print(rep["reason"])
    if rep["auc_current"] is not None:
        t = Table("term", "current", "tuned")
        for k in ("sound", "tempo", "key", "energy", "mood", "lyrics", "popularity"):
            t.add_row(k, f"{rep['current'][k]:.2f}", f"{rep['tuned'][k]:.2f}")
        console.print(t)
    if rep["apply"] and not dry_run:
        console.print("[green]applied — new bridges use the tuned scoring")


if __name__ == "__main__":
    app()
