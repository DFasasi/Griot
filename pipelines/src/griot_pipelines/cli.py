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


if __name__ == "__main__":
    app()


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
