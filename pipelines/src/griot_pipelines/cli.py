"""griot-enrich — add popularity, previews and lyric features to analyzed tracks."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress

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


@app.command("fix-tempo")
def fix_tempo(
    dsn: str = typer.Option(os.environ.get("DATABASE_URL", ""), help="Postgres DSN (to read the catalog)"),
    api: str = typer.Option(os.environ.get("GRIOT_API_URL", "http://127.0.0.1:8000")),
    token: str = typer.Option(os.environ.get("GRIOT_SUBMIT_TOKEN", ""), help="Submit token for the API"),
    dry_run: bool = typer.Option(False, help="Report what would change without writing"),
) -> None:
    """Correct preview-analysed tempos using Deezer's published BPM.

    Early preview runs trusted the structure model's tempo, which is unreliable on 30 s clips.
    Each correction is resubmitted through the API so the merged catalog stays consistent.
    """
    import httpx

    from griot_analyzer.extract import reconcile_bpm
    from griot_api.repo import PgRepo
    from griot_pipelines.sources import Deezer

    repo, dz = PgRepo(dsn), Deezer()
    tracks = [t for t in repo.all_tracks() if t.tier == "B" and t.external_ids.get("deezer")]
    fixed, unchanged, no_bpm = [], 0, 0
    with Progress(console=console) as prog:
        task = prog.add_task("checking tempos", total=len(tracks))
        for t in tracks:
            prog.advance(task)
            try:
                d = dz._get(f"/track/{t.external_ids['deezer']}") or {}
            except Exception:
                continue
            hint = d.get("bpm") or 0
            if not hint:
                no_bpm += 1
                continue
            new = round(reconcile_bpm(t.global_.bpm, t.global_.bpm, hint), 2)
            if abs(new - t.global_.bpm) / t.global_.bpm > 0.04:
                t.global_.bpm = new
                fixed.append(t)
            else:
                unchanged += 1
    console.print(
        f"{len(fixed)} tempos corrected · {unchanged} already right · {no_bpm} without a Deezer BPM"
    )
    for t in fixed[:8]:
        console.print(f"  {t.artist} — {t.title}: now {t.global_.bpm:.0f} BPM")
    if dry_run or not fixed:
        return
    with httpx.Client(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=300) as c:
        for i in range(0, len(fixed), 25):
            docs = [t.model_dump(mode="json", by_alias=True) for t in fixed[i : i + 25]]
            c.post("/submissions", json=docs).raise_for_status()
    console.print(f"resubmitted {len(fixed)} corrected tracks")


if __name__ == "__main__":
    app()
