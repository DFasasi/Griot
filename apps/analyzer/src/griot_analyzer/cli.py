"""griot — local-first full-song analyzer. Audio never leaves this machine; only features do."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import traceback
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TimeRemainingColumn
from rich.table import Table

from griot_analyzer.identify import FileIdentity, identify, iter_audio, track_id
from griot_analyzer.store import DEFAULT_HOME, Store
from griot_core.env import load_dotenv

app = typer.Typer(help=__doc__, no_args_is_help=True)
load_dotenv()
console = Console()
HomeOpt = typer.Option(DEFAULT_HOME, "--home", help="Analyzer state directory")


@app.command()
def scan(root: Path, home: Path = HomeOpt) -> None:
    """Find audio under ROOT, read tags, fingerprint, and resolve MusicBrainz IDs."""
    store = Store(home)
    key = os.environ.get("ACOUSTID_API_KEY")
    if not key:
        console.print("[yellow]ACOUSTID_API_KEY not set: using embedded tags only for identification")
    files = list(iter_audio(root.expanduser()))
    added = 0
    with Progress(*Progress.get_default_columns(), MofNCompleteColumn(), console=console) as prog:
        task = prog.add_task("scanning", total=len(files))
        for p in files:
            try:
                ident = identify(p, key)
                if not store.known(ident.sha1):
                    added += 1
                store.add(ident)
            except Exception as e:  # unreadable file: report and keep going
                console.print(f"[red]skip {p}: {e}")
            prog.advance(task)
            if key:
                time.sleep(0.34)  # AcoustID allows 3 requests/second
    console.print(f"{len(files)} files, {added} new. Run [bold]griot analyze[/] next.")


@app.command()
def analyze(
    home: Path = HomeOpt,
    limit: int = typer.Option(0, help="Max files this run (0 = all)"),
    batch: int = typer.Option(8, help="Files per structure-model batch"),
    retry_failed: bool = typer.Option(False, help="Retry previously failed files"),
) -> None:
    """Run full-song analysis on pending files. Safe to interrupt and resume."""
    from griot_analyzer.extract import (
        Models,
        analyze_descriptors,
        analyze_structure,
        build_features,
        window_embeddings,
        zero_shot,
    )
    from griot_analyzer.quality import assess
    from griot_core.schema import TrackFeatures

    store = Store(home)
    rows = store.pending(limit or None, retry_failed)
    if not rows:
        console.print("Nothing to analyze.")
        return
    models = Models()
    work = Path(tempfile.mkdtemp(prefix="griot-"))
    started = time.time()
    try:
        with Progress(
            "[progress.description]{task.description}", BarColumn(), MofNCompleteColumn(),
            TimeRemainingColumn(), console=console,
        ) as prog:  # fmt: skip
            task = prog.add_task("analyzing", total=len(rows))
            for i in range(0, len(rows), batch):
                chunk = rows[i : i + batch]
                paths = [Path(r["path"]) for r in chunk]
                try:
                    structs = analyze_structure(paths, work)
                except Exception:
                    structs = {}  # fall back to per-file so one bad file doesn't sink the batch
                for row, path in zip(chunk, paths, strict=True):
                    prog.update(task, description=path.name[:40])
                    try:
                        ident = FileIdentity(**json.loads(row["identity"]))
                        struct = structs.get(path) or analyze_structure([path], work)[path]
                        desc = analyze_descriptors(path)
                        centres, embs = window_embeddings(path, models)
                        zs = zero_shot(embs, models)
                        feats = build_features(path, struct, desc, centres, embs, zs)
                        tf = TrackFeatures(
                            id=track_id(ident),
                            mbid=ident.mbid,
                            isrc=ident.isrc,
                            title=ident.title,
                            artist=ident.artist,
                            artist_ids=ident.artist_mbids,
                            year=ident.year,
                            tier="A",
                            **feats,
                        )
                        q = assess(path, desc, struct)
                        tf.external_ids |= {f"quality_{k}": str(v) for k, v in q.items()}
                        store.done(ident.sha1, tf.model_dump_json(by_alias=True))
                    except Exception as e:
                        store.failed(row["sha1"], f"{e}\n{traceback.format_exc()}")
                        console.print(f"[red]failed {path.name}: {e}")
                    prog.advance(task)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    dt = time.time() - started
    console.print(f"Done in {dt / 60:.1f} min ({dt / len(rows):.1f}s/track). {store.counts()}")


@app.command()
def status(home: Path = HomeOpt) -> None:
    """Show analysis progress."""
    t = Table("status", "files")
    for k, v in sorted(Store(home).counts().items()):
        t.add_row(k, str(v))
    console.print(t)


@app.command()
def export(out: Path, home: Path = HomeOpt) -> None:
    """Write all analyzed TrackFeatures to a JSONL file."""
    feats = Store(home).all_features()
    out.write_text("\n".join(feats) + "\n")
    console.print(f"wrote {len(feats)} tracks to {out}")


@app.command()
def submit(
    home: Path = HomeOpt,
    api: str = typer.Option(os.environ.get("GRIOT_API_URL", "http://localhost:8000")),
    token: str = typer.Option(os.environ.get("GRIOT_SUBMIT_TOKEN", "dev-token")),
) -> None:
    """Upload features (never audio) to the Griot catalog."""
    store = Store(home)
    rows = store.unsubmitted()
    sent = 0
    with httpx.Client(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=60) as c:
        for i in range(0, len(rows), 50):
            chunk = rows[i : i + 50]
            r = c.post("/submissions", json=[json.loads(x["features"]) for x in chunk])
            r.raise_for_status()
            store.mark_submitted([x["sha1"] for x in chunk])
            sent += len(chunk)
    console.print(f"submitted {sent} tracks to {api}")


@app.command()
def bridge(
    start: str,
    end: str,
    n: int = typer.Option(8, "-n", help="Bridge tracks between START and END"),
    home: Path = HomeOpt,
    m3u: Path | None = typer.Option(None, help="Write an M3U playlist of your local files"),
) -> None:
    """Bridge two songs in YOUR analyzed library (title/artist substring match)."""
    from griot_core import Catalog, Pathfinder
    from griot_core.schema import TrackFeatures

    store = Store(home)
    rows = store.db.execute("SELECT path, features FROM files WHERE status='done'").fetchall()
    tracks = [TrackFeatures.model_validate_json(r["features"]) for r in rows]
    paths = {t.id: r["path"] for t, r in zip(tracks, rows, strict=True)}
    cat = Catalog(tracks)

    def find(q: str) -> int:
        ql = q.lower()
        hits = [i for i, t in enumerate(tracks) if ql in f"{t.title} {t.artist}".lower()]
        if not hits:
            raise typer.BadParameter(f"no analyzed track matches {q!r}")
        return hits[0]

    leg = Pathfinder(cat, max_per_artist=2).bridge([find(start), find(end)], [min(n, len(cat) - 2)])
    t = Table("#", "track", "bpm", "key", "seam cost", "biggest term")
    for k, idx in enumerate(leg.path):
        tr = tracks[idx]
        if k == 0:
            t.add_row("1", f"{tr.title} — {tr.artist}", f"{tr.global_.bpm:.0f}", tr.global_.camelot, "", "")
            continue
        tx = leg.transitions[k - 1]
        worst = max(tx["terms"], key=tx["terms"].get)
        t.add_row(str(k + 1), f"{tr.title} — {tr.artist}", f"{tr.global_.bpm:.0f}", tr.global_.camelot,
                  f"{tx['cost']:.2f}", worst)  # fmt: skip
    console.print(t)
    if m3u:
        m3u.write_text("#EXTM3U\n" + "\n".join(paths[tracks[i].id] for i in leg.path) + "\n")
        console.print(f"wrote {m3u}")


@app.command("app")
def app_cmd(
    port: int = typer.Option(51735, help="Loopback port for the desktop UI"),
    home: Path = HomeOpt,
    browser: bool = typer.Option(True, help="Open the UI in your browser"),
) -> None:
    """Run the Griot desktop agent (UI + local analyzer) on 127.0.0.1."""
    from griot_analyzer.agent import serve

    console.print(f"Griot running at [bold]http://127.0.0.1:{port}[/]")
    serve(port=port, home=home, open_browser=browser)


if __name__ == "__main__":
    app()
