"""griot-enrich — add popularity, previews and lyric features to analyzed tracks."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress

from griot_core.schema import TrackFeatures
from griot_pipelines.enrich import Enricher, EnrichStats

app = typer.Typer(help=__doc__, no_args_is_help=True)
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
