"""Local desktop agent: one loopback server for the desktop app.

    /            the exported web UI (apps/web/out)
    /agent/*     analyzer controls: pick folder, scan, analyze (background), submit, library
    /api/*       proxy to the Griot API (cloud or local)

Serving everything from http://127.0.0.1:<port> keeps the desktop and web builds identical
and makes OAuth loopback redirects (Spotify) work without special cases.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from griot_analyzer.identify import FileIdentity, identify, iter_audio, track_id
from griot_analyzer.store import DEFAULT_HOME, Store

DEFAULT_PORT = 51735
WEB_OUT = Path(os.environ.get("GRIOT_WEB_DIR", Path(__file__).resolve().parents[3] / "web" / "out"))


@dataclass
class Job:
    kind: str = "idle"  # idle | scanning | analyzing | submitting
    total: int = 0
    done: int = 0
    current: str = ""
    started: float = 0.0
    errors: list[str] = field(default_factory=list)
    stop: bool = False


class Agent:
    def __init__(self, home: Path, api_url: str, token: str) -> None:
        self.home, self.api_url, self.token = home, api_url, token
        self.job = Job()
        self.lock = threading.Lock()

    def store(self) -> Store:  # sqlite connections are per-thread
        return Store(self.home)

    def _run(self, kind: str, fn) -> None:
        with self.lock:
            if self.job.kind != "idle":
                raise HTTPException(409, f"busy: {self.job.kind}")
            self.job = Job(kind=kind, started=time.time())

        def wrapper():
            try:
                fn()
            except Exception as e:
                self.job.errors.append(f"{e}\n{traceback.format_exc()}"[:2000])
            finally:
                self.job.kind = "idle"
                self.job.current = ""

        threading.Thread(target=wrapper, daemon=True).start()

    def scan(self, root: Path) -> None:
        def go():
            store, key = self.store(), os.environ.get("ACOUSTID_API_KEY")
            files = list(iter_audio(root))
            self.job.total = len(files)
            for p in files:
                if self.job.stop:
                    break
                self.job.current = p.name
                try:
                    store.add(identify(p, key))
                    if key:
                        time.sleep(0.34)
                except Exception as e:
                    self.job.errors.append(f"{p.name}: {e}")
                self.job.done += 1

        self._run("scanning", go)

    def analyze(self, limit: int | None = None) -> None:
        def go():
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

            store = self.store()
            rows = store.pending(limit)
            self.job.total = len(rows)
            models, work = Models(), self.home / "work"
            for row in rows:
                if self.job.stop:
                    break
                path = Path(row["path"])
                self.job.current = path.name
                try:
                    ident = FileIdentity(**json.loads(row["identity"]))
                    struct = analyze_structure([path], work)[path]
                    desc = analyze_descriptors(path)
                    centres, embs = window_embeddings(path, models)
                    feats = build_features(path, struct, desc, centres, embs, zero_shot(embs, models))
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
                    tf.external_ids |= {f"quality_{k}": str(v) for k, v in assess(path, desc, struct).items()}
                    store.done(ident.sha1, tf.model_dump_json(by_alias=True))
                except Exception as e:
                    store.failed(row["sha1"], f"{e}\n{traceback.format_exc()}")
                    self.job.errors.append(f"{path.name}: {e}")
                self.job.done += 1

        self._run("analyzing", go)

    def submit(self) -> None:
        def go():
            store = self.store()
            rows = store.unsubmitted()
            self.job.total = len(rows)
            if not self.token:
                raise RuntimeError("GRIOT_SUBMIT_TOKEN is not set; add it to .env to share features")
            headers = {"Authorization": f"Bearer {self.token}"}
            with httpx.Client(base_url=self.api_url, headers=headers, timeout=60) as c:
                for i in range(0, len(rows), 25):
                    chunk = rows[i : i + 25]
                    r = c.post("/submissions", json=[json.loads(x["features"]) for x in chunk])
                    if r.status_code >= 400:
                        self.job.errors.append(f"submit failed: {r.status_code} {r.text[:300]}")
                        break
                    store.mark_submitted([x["sha1"] for x in chunk])
                    self.job.done += len(chunk)

        self._run("submitting", go)


def pick_folder() -> str | None:
    """Native folder dialog. macOS via AppleScript; elsewhere Tk."""
    if sys.platform == "darwin":
        r = subprocess.run(
            ["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose your music folder")'],
            capture_output=True,
            text=True,
        )
        return r.stdout.strip() or None
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        return filedialog.askdirectory(title="Choose your music folder") or None
    except Exception:
        return None


class ScanBody(BaseModel):
    path: str | None = None  # None -> open the native folder picker


def create_agent_app(agent: Agent) -> FastAPI:
    app = FastAPI(title="Griot agent")
    proxy = httpx.AsyncClient(base_url=agent.api_url, timeout=120)

    @app.get("/agent/status")
    def status() -> dict:
        j = agent.job
        rate = j.done / (time.time() - j.started) if j.kind != "idle" and j.done else None
        return {
            "desktop": True,
            "job": asdict(j) | {"errors": j.errors[-5:], "per_track_s": round(1 / rate, 1) if rate else None},
            "counts": agent.store().counts(),
            "api": agent.api_url,
        }

    @app.post("/agent/scan")
    def scan(body: ScanBody) -> dict:
        path = body.path or pick_folder()
        if not path:
            raise HTTPException(400, "no folder chosen")
        if not Path(path).expanduser().is_dir():
            raise HTTPException(400, f"not a folder: {path}")
        agent.scan(Path(path).expanduser())
        return {"scanning": path}

    @app.post("/agent/analyze")
    def analyze(limit: int | None = None) -> dict:
        agent.analyze(limit)
        return {"analyzing": True}

    @app.post("/agent/submit")
    def submit() -> dict:
        agent.submit()
        return {"submitting": True}

    @app.post("/agent/stop")
    def stop() -> dict:
        agent.job.stop = True
        return {"stopping": True}

    @app.get("/agent/library")
    def library() -> list[dict]:
        out = []
        for r in agent.store().db.execute(
            "SELECT sha1, path, identity, status, features, submitted FROM files"
        ):
            ident = json.loads(r["identity"])
            row = {
                "sha1": r["sha1"],
                "title": ident["title"],
                "artist": ident["artist"],
                "status": r["status"],
                "submitted": bool(r["submitted"]),
                "duration_s": ident.get("duration_s"),
                "isrc": ident.get("isrc"),
            }
            if r["features"]:
                f = json.loads(r["features"])
                q = {k[8:]: v for k, v in f.get("external_ids", {}).items() if k.startswith("quality_")}
                row |= {"bpm": f["global"]["bpm"], "camelot": f["global"]["camelot"], "quality": q}
            out.append(row)
        return out

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def api(path: str, request: Request) -> Response:
        r = await proxy.request(
            request.method,
            f"/{path}",
            params=request.query_params,
            content=await request.body(),
            headers={
                k: v for k, v in request.headers.items() if k.lower() in {"content-type", "authorization"}
            },
        )
        return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))

    @app.get("/{path:path}")
    def ui(path: str) -> Response:
        if not WEB_OUT.is_dir():
            raise HTTPException(503, f"web UI not built at {WEB_OUT} (run: cd apps/web && npm run export)")
        target = (WEB_OUT / path).resolve()
        if WEB_OUT.resolve() not in target.parents and target != WEB_OUT.resolve():
            raise HTTPException(404)
        for cand in (target, target / "index.html", target.with_suffix(".html")):
            if cand.is_file():
                return FileResponse(cand)
        return FileResponse(WEB_OUT / "404.html", status_code=404)

    return app


def _exit_with_parent(pid: int) -> None:
    """Exit when the desktop shell that started us is gone (quit, crash or force-kill), so the
    agent never outlives the app. Polls; works the same on macOS, Linux and Windows."""

    def alive() -> bool:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    def watch() -> None:
        while alive():
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def serve(port: int = DEFAULT_PORT, home: Path = DEFAULT_HOME, open_browser: bool = False) -> None:
    import uvicorn

    if parent := os.environ.get("GRIOT_PARENT_PID"):
        _exit_with_parent(int(parent))

    agent = Agent(
        home,
        os.environ.get("GRIOT_API_URL", "http://127.0.0.1:8000"),
        os.environ.get("GRIOT_SUBMIT_TOKEN", ""),
    )
    if open_browser:
        threading.Timer(1.0, lambda: __import__("webbrowser").open(f"http://127.0.0.1:{port}")).start()
    level = os.environ.get("GRIOT_AGENT_LOG_LEVEL", "warning")
    uvicorn.run(create_agent_app(agent), host="127.0.0.1", port=port, log_level=level)
