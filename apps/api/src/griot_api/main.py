"""Griot HTTP API.

Backend selection (env):
  DATABASE_URL       -> Postgres (+pgvector)
  GRIOT_SEED_JSONL   -> in-memory, seeded from an analyzer export (`griot export`)
  (neither)          -> in-memory synthetic catalog, for UI development
"""

import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from griot_api.repo import MemoryRepo, PgRepo, Repo
from griot_api.service import BridgeError, BridgeService
from griot_core.env import load_dotenv
from griot_core.schema import BridgeRequest, BridgeResponse, ImportItem, Resolution, TrackFeatures


def make_repo() -> Repo:
    if dsn := os.environ.get("DATABASE_URL"):
        return PgRepo(dsn)
    if seed := os.environ.get("GRIOT_SEED_JSONL"):
        return MemoryRepo.from_jsonl(Path(seed))
    from griot_core.synthetic import make_tracks

    return MemoryRepo(make_tracks(n=int(os.environ.get("GRIOT_SYNTHETIC_N", "3000")), seed=7))


def make_text_embedder():
    """MuQ-MuLan text tower for prompt steering, if the analyzer's ML extras are installed."""
    if os.environ.get("GRIOT_TEXT_STEERING", "1") != "1":
        return None
    try:
        from griot_analyzer.extract import Models
    except ImportError:
        return None
    models = Models()
    return models.embed_text


def create_app(service: BridgeService | None = None) -> FastAPI:
    app = FastAPI(title="Griot", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("GRIOT_CORS", "http://localhost:3000").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.service = service

    def svc() -> BridgeService:
        if app.state.service is None:
            app.state.service = BridgeService(make_repo(), make_text_embedder())
        return app.state.service

    Svc = Annotated[BridgeService, Depends(svc)]
    tokens = set(os.environ.get("GRIOT_SUBMIT_TOKENS", "dev-token").split(","))

    def submitter(authorization: Annotated[str | None, Header()] = None) -> str:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token not in tokens:
            raise HTTPException(401, "invalid submit token")
        return token

    @app.get("/health")
    def health(s: Svc) -> dict:
        return {"ok": True, "tracks": len(s.catalog)}

    @app.get("/search")
    def search(s: Svc, q: Annotated[str, Query(min_length=1)], limit: int = 10) -> list[dict]:
        return s.search(q, min(limit, 50))

    @app.get("/tracks/{track_id}")
    def track(s: Svc, track_id: str) -> dict:
        try:
            t = s.track(track_id)
        except KeyError:
            raise HTTPException(404, "track not found") from None
        return t.model_dump(mode="json", by_alias=True, exclude={"embeddings", "lyrics"})

    @app.post("/submissions")
    def submissions(s: Svc, docs: list[TrackFeatures], who: Annotated[str, Depends(submitter)]) -> dict:
        if len(docs) > 500:
            raise HTTPException(413, "max 500 tracks per request")
        try:
            return {"accepted": s.submit(who, docs)}
        except BridgeError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/bridges")
    def create_bridge(s: Svc, req: BridgeRequest) -> BridgeResponse:
        try:
            resp = s.bridge(req)
        except BridgeError as e:
            raise HTTPException(422, str(e)) from None
        resp.id = s.repo.save_bridge(req.model_dump(mode="json"), resp.model_dump(mode="json"))
        return resp

    @app.get("/bridges/{bridge_id}")
    def get_bridge(s: Svc, bridge_id: str) -> dict:
        b = s.repo.get_bridge(bridge_id)
        if b is None:
            raise HTTPException(404, "bridge not found")
        return b["response"] | {"id": bridge_id}

    class Feedback(BaseModel):
        position: int
        rating: Literal[-1, 1]

    @app.post("/bridges/{bridge_id}/feedback", status_code=204)
    def feedback(s: Svc, bridge_id: str, fb: Feedback) -> None:
        try:
            s.repo.feedback(bridge_id, fb.position, fb.rating)
        except KeyError:
            raise HTTPException(404, "bridge not found") from None

    @app.get("/bridges/{bridge_id}/m3u", response_class=PlainTextResponse)
    def m3u(s: Svc, bridge_id: str) -> str:
        b = s.repo.get_bridge(bridge_id)
        if b is None:
            raise HTTPException(404, "bridge not found")
        lines = ["#EXTM3U"]
        for t in b["response"]["tracks"]:
            lines += [f"#EXTINF:-1,{t['artist']} - {t['title']}", f"{t['artist']} - {t['title']}"]
        return "\n".join(lines) + "\n"

    # ---------------------------------------------------------------- library import

    @app.post("/resolve")
    def resolve(s: Svc, items: list[ImportItem]) -> list[Resolution]:
        """Map songs from any platform onto catalog recordings and report their coverage."""
        if len(items) > 200:
            raise HTTPException(413, "max 200 items per request")
        return s.resolve(items)

    @app.get("/import/youtube")
    def import_youtube(playlist: Annotated[str, Query(min_length=5)]) -> list[ImportItem]:
        from griot_pipelines.youtube import fetch_playlist

        try:
            return fetch_playlist(playlist)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from None

    @app.get("/wanted")
    def wanted(s: Svc, who: Annotated[str, Depends(submitter)], limit: int = 100) -> list[dict]:
        """Imported songs nobody has analysed yet, most-requested first."""
        return s.repo.wanted(min(limit, 1000))

    @app.get("/config")
    def config() -> dict:
        """Public, non-secret client settings (OAuth client ids are public by design)."""
        return {
            "spotify_client_id": os.environ.get("SPOTIFY_CLIENT_ID"),
            "youtube_import": bool(os.environ.get("YOUTUBE_API_KEY")),
        }

    return app


load_dotenv()
app = create_app()
