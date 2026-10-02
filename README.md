# Griot — Sonic Bridge

Give Griot two or more songs; it builds the album that connects them. Every track is
analysed **as a full song** (structure, intro/outro, energy and mood over time), so a
transition is judged on how song A actually *ends* and song B actually *begins*.

Plan and rationale: [docs/ROADMAP.md](docs/ROADMAP.md) · Model spike: [docs/spike-results.md](docs/spike-results.md)

## Layout

| Path | What |
|---|---|
| `packages/griot_core` | Feature schema/contracts, Camelot + tempo theory, seam-aware cost, Viterbi pathfinder, evaluation |
| `apps/analyzer` | `griot` CLI — local full-song analysis (audio never leaves the machine) |
| `apps/api` | FastAPI — search, bridges, submissions, feedback, M3U |
| `apps/web` | Next.js UI — Studio, Library (Spotify/YouTube/files), interactive About page; one build for web and desktop |
| `apps/desktop` | Tauri desktop shell — native window around the same UI plus the local analyzer agent |
| `pipelines` | `griot-enrich` (popularity, Deezer previews, LRCLIB lyrics → embeddings/themes) and the Modal corpus job |
| `eval` | Griot vs baselines, incl. the preview-only ablation |
| `db/migrations` | Postgres + pgvector schema |

## Quickstart

```bash
brew install uv chromaprint ffmpeg        # Python 3.12 is managed by uv
uv sync --all-packages --all-extras       # includes ML models (torch, MuQ, Essentia, all-in-one)
uv run pytest -q

# 1. analyse your own music (resumable; ~40–70 s per song on Apple Silicon)
uv run griot scan ~/Music
uv run griot analyze
uv run griot bridge "song a" "song b" -n 8 --m3u bridge.m3u    # works offline, on your library

# 2. enrich + serve
uv run griot export data/mine.jsonl
uv run griot-enrich jsonl data/mine.jsonl data/mine.enriched.jsonl
GRIOT_SEED_JSONL=data/mine.enriched.jsonl uv run uvicorn griot_api.main:app --port 8000
cd apps/web && npm install && npm run dev                      # http://localhost:3000
```

With no `GRIOT_SEED_JSONL` / `DATABASE_URL`, the API serves a synthetic catalog for UI work.
For Postgres: `docker compose up -d db`, then `DATABASE_URL=postgresql://griot:griot@localhost:5432/griot`.

## Desktop app

The desktop app is the same UI plus the local analyzer, served from `http://127.0.0.1:51735`
by the agent (`griot app`). The Tauri shell starts the agent and opens a native window; the
agent shuts itself down when the app closes, even after a crash.

```bash
cd apps/web && npm run export                 # static UI -> apps/web/out
uv run griot app                               # agent + UI in your browser, or:
cd apps/desktop && npm install && npm run dev  # native window (Rust toolchain required)
```

Not done yet: a self-contained installer. The shell currently runs the agent through `uv`
from this repo; packaging needs the agent frozen into a bundled sidecar (`GRIOT_AGENT_CMD`).

## Spotify, YouTube, and your files

- **Spotify:** create an app at developer.spotify.com, add redirect URIs
  `http://127.0.0.1:3000/library` (web dev) and `http://127.0.0.1:51735/library` (desktop),
  and set `SPOTIFY_CLIENT_ID` on the API. Sign-in is PKCE, so there's no client secret. Since
  Feb 2026, development-mode apps require Premium, allow 5 users, and get no ISRCs — Griot
  matches by title/artist/duration instead.
- **YouTube:** set `YOUTUBE_API_KEY` on the API; users paste a playlist link. Songs imported
  from YouTube play in full through YouTube's embedded player. Griot never downloads audio.
- **Files:** analysed locally by the desktop app; only features are shared. Low-bitrate or
  re-encoded files are flagged, and merges prefer clean sources.
- Songs nobody has analysed are queued; `uv run griot-enrich previews` fills them from 30 s
  Deezer previews (marked preview-only) until a full-song analysis replaces them.

Run the dev web app on `127.0.0.1` (not `localhost`) so Spotify's loopback redirect matches.

## Keys and accounts (all optional, all free tiers)

| Env var | Unlocks |
|---|---|
| `ACOUSTID_API_KEY` | Fingerprint → MusicBrainz ID for untagged files |
| `LASTFM_API_KEY` | Last.fm play counts (the ≥5,000-plays gate); ListenBrainz/Deezer work without keys |
| `ANTHROPIC_API_KEY` (or `ant auth login`) | Lyric theme tags (Claude Haiku 4.5) |
| `SPOTIFY_CLIENT_ID` | Spotify library import |
| `YOUTUBE_API_KEY` | YouTube playlist import |
| Modal account + `modal secret create jamendo JAMENDO_CLIENT_ID=…` | GPU backfill of the open full-track corpus |

## Evaluate

```bash
uv run python eval/run_eval.py data/catalog.jsonl --pairs 100 --n 8
```
