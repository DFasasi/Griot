# Griot — End-to-End Development Plan (Sonic Bridge)

## Context

Griot takes songs from a user (2+ waypoints, later a drawn mood/energy arc) and generates a "bridge" album: an ordered sequence of real tracks that moves smoothly from one song to the next — sonically, harmonically, rhythmically, and narratively (lyrics). The defining requirement is **analysis of full songs, not 30-second snippets**: a good transition depends on how song A *ends* and song B *begins*, and on each song's internal structure, which previews can't show.

The repo was empty, so this is a greenfield plan. It refines an earlier ChatGPT/Gemini blueprint after checking its claims against current sources. **Decisions made:** local-first analyzer for full audio; waypoints first, arc-drawing later; solo portfolio MVP on a low budget.

### Corrections to the earlier blueprint (verified Oct 2026)
| Earlier claim | Reality | Consequence |
|---|---|---|
| Last.fm-360K gives track play counts | It's **artist-level** only | Use **Last.fm `track.getInfo` playcount** + **ListenBrainz popular-recordings** (track-level, CC0, keyed by MBID) |
| Browser can analyze tracks "streamed from a service" | Spotify/Apple streams are **DRM-encrypted**; inaccessible to Web Audio | Full-song analysis must use **files the user owns** |
| Spotify OAuth export is easy | Feb 2026: dev mode requires Premium, **max 5 test users**; extended quota needs a registered business + 250k MAU | Spotify export is fine for a demo; add M3U/local export and Apple Music |
| Concatenate MERT + SBERT + metadata into one vector | Mixed scales let one modality dominate, and you lose per-modality control | Keep **separate vectors per modality** and combine them in a weighted cost |
| A* beam search over ANN neighbors | Fixed-length A→B with a smooth progression is better solved as a **layered DP (Viterbi)** | See §4 — exact within candidates, fast, easy to tune |
| MERT as the audio embedding | **MuQ / MuQ-MuLan** (2025) beats MERT/MusicFM; MuLan variant is a **joint music–text space** | Enables text steering ("darker", "more acoustic") for free |

AcousticBrainz is **evidence for this plan**: it did exactly this, with users running an extractor on their own full-length files and submitting features. Its 2022 dump (~30M submissions, ~7.5M recordings) is still downloadable, so Griot is effectively "AcousticBrainz reborn with modern models + a pathfinder."

### Where Griot beats Spotify/Apple
Their recommenders mostly use collaborative filtering and optimize for engagement and familiarity. They don't handle intent ("get me from A to B"), they don't consider transitions (outro→intro), they don't use song structure, they don't offer a controllable energy/mood arc, and they don't consider lyrical narrative. Spotify also removed `audio-features`, `audio-analysis`, `recommendations`, and `related-artists` for new apps (Nov 2024), so these features can't be rebuilt on its API.

## Status (2026-10-02)

| Milestone | State |
|---|---|
| M0 scaffold, contracts, CI | Done |
| Model spike | Done — see [spike-results.md](spike-results.md) |
| Analyzer v1 (scan/analyze/export/submit/local bridge) | Done, verified on real audio |
| Enrichment (Deezer, ListenBrainz, Last.fm, MusicBrainz, LRCLIB, themes) | Done, verified live; Last.fm + themes need keys |
| Modal corpus backfill | Written and import-validated; needs a Modal account + Jamendo key to run |
| Bridge engine + eval harness + baselines | Done; synthetic sanity run in `eval/results-synthetic.md` |
| API (memory + Postgres) | Done, Postgres path tested against pg17 + pgvector |
| Web app | Done, rendered and checked in headless Chrome (light + dark) |
| Spotify/Apple export, arc drawing UI, render-mix | Next |
| Real-catalog eval + listening test + deploy | Next (needs the corpus/library runs) |

Deviations from the plan: plain SQL migration instead of alembic (one schema file so far);
`all-in-one-mlx` / `all-in-one-infer` instead of upstream `allin1` (broken with current NATTEN);
valence/arousal/tags are zero-shot from MuQ-MuLan for v1 (a head trained on DEAM/PMEmo is a later upgrade);
the API bridges over an in-memory catalog and keeps pgvector HNSW for when the catalog outgrows RAM.

---

## 1. Architecture overview

```
 User's machine                         Cloud                                 Browser
┌──────────────────────┐   features   ┌───────────────────────────┐  JSON  ┌──────────────────┐
│ griot-analyzer (CLI) │ ───────────▶ │ FastAPI  ─── Postgres     │ ◀────▶ │ Next.js web app  │
│ scan → fingerprint → │  (no audio)  │  ingest      + pgvector   │        │ waypoints, arc,  │
│ full-song analysis   │              │  bridge      (catalog,    │        │ bridge viz,      │
│ → submit             │              │  export       vectors)    │        │ preview, export  │
└──────────────────────┘              └───────────▲───────────────┘        └──────────────────┘
                                                  │ batch backfill
                                  Modal (GPU): open full-track corpora, AcousticBrainz import,
                                  ListenBrainz/Last.fm popularity, LRCLIB lyrics, Deezer metadata
```

Stack: Python 3.12 monorepo (uv workspaces) · PyTorch (MPS on Apple Silicon) · Essentia + essentia-tensorflow models · allin1 · MuQ-MuLan · Postgres 16 + pgvector (HNSW) · FastAPI · Next.js + TypeScript + D3 · Modal for batch GPU · Docker Compose for local dev.

### Repo layout
```
griot/
  packages/griot_core/      # shared: feature schema (pydantic), Camelot math, cost fn, pathfinder
  apps/analyzer/            # local CLI: scan, fingerprint, analyze, submit, render-mix
  apps/api/                 # FastAPI: ingest, search, bridge, export
  apps/web/                 # Next.js UI
  pipelines/                # catalog bootstrap jobs (listenbrainz, lastfm, acousticbrainz, lrclib, deezer, corpora) + modal_app.py
  db/migrations/            # alembic
  eval/                     # metrics, baselines, listening-test harness
  docs/ROADMAP.md           # this plan, committed
  docker-compose.yml
```

---

## 2. Data acquisition — full songs, legally

**Track identity:** the canonical key is the MusicBrainz **recording MBID**, with ISRC as a secondary key. The analyzer identifies local files with **Chromaprint → AcoustID** (free for non-commercial use), falling back to embedded tags and a MusicBrainz search.

Catalog tiers (each stored track has `analysis_tier` and `analyzer_version`):

| Tier | Source | Audio coverage | Role |
|---|---|---|---|
| **A** | `griot-analyzer` on users' own files (start with your library + friends) | **Full song** | Primary bridge candidates |
| **A-open** | FMA-full (~106k CC tracks), MTG-Jamendo (~55k full tracks), run on Modal | Full song | Algorithm development/tuning, unlimited reprocessing |
| **C** (post-MVP) | AcousticBrainz dump, filtered to popular MBIDs | Full-song *summary stats* (no time series, older models) | Coverage: global BPM/key/mood for millions; candidate expansion; marked "coarse" |
| **B** | Deezer API (rank, BPM, gain, ISRC, 30s preview URL) | 30s (fallback only) | Metadata, in-app previews; optional preview embedding for gap-filling, flagged "partial" |

**Popularity (≥5,000 plays):** the gate is **Last.fm `track.getInfo` playcount ≥ 5000**, cross-checked against the ListenBrainz `popular-recordings-by-listen-count` dataset (MBID-keyed, CC0). Store `lastfm_playcount`, `lb_listens`, and `deezer_rank`. Bridges choose from popular tracks by default; a "deep cuts" slider loosens the gate.

**Lyrics:** **LRCLIB** is free, needs no API key, provides *time-synced* lyrics, and publishes a downloadable SQLite dump (~19 GB). Synced timestamps let the system judge lyrical content near the outro/intro, not just whole-song averages. Genius and Musixmatch are not used because of ToS and cost.

Not used: yt-dlp/YouTube ripping (violates ToS), Musicae/FreqBlog (global averages only, no temporal or structural data, adds cost and dependency). These could be added later to fill tier-B metadata.

**Cold-start reality:** tier-A popular coverage starts at whatever your and your testers' libraries contain (target 20–50k tracks for MVP). Tier C widens the candidate pool while that grows. The UI shows coverage honestly ("full analysis" vs "coarse" badges).

---

## 3. Full-song feature extraction (`griot-analyzer` + shared pipeline)

The same `griot_core.extract` code runs locally (analyzer) and on Modal (corpora). Every output is versioned (`analyzer_version`, model hashes). Per track:

**3a. Structure (the reason full songs matter)** — `allin1`: beats, downbeats, BPM, and functional segments (intro/verse/chorus/bridge/outro) with timestamps. It uses Demucs source separation internally: slow on CPU, OK on MPS/GPU. An Apple-Silicon MLX port exists if speed matters.

**3b. Global descriptors** — Essentia: key/mode (KeyExtractor, `edma` profile) → Camelot code; integrated loudness (LUFS); danceability; duration; tuning frequency. Essentia TF heads (discogs-effnet backbone) provide genre/mood/instrumentation tags plus **valence/arousal** regressors.

**3c. Temporal trajectories** (aligned to beats, resampled to fixed-length 2s frames): energy (short-term LUFS), onset density, spectral centroid/brightness, 12-d chroma, and valence/arousal per ~10s window. Stored as compact float16 arrays (JSONB or bytea).

**3d. Embeddings** (MuQ-MuLan, 512-d, L2-normalized), computed over windows and pooled into:
- `emb_full` — mean over the whole song (identity of the track)
- `emb_intro` — first ~30s or the allin1 `intro` segment
- `emb_outro` — last ~30s or the allin1 `outro` segment
- `emb_chorus` — the most-repeated segment (the track's "hook" sound)

Transitions compare **outro(A) ↔ intro(B)**, which preview-based systems can't do.

**3e. Lyrics** (server side, from LRCLIB): a sentence-embedding model (e.g. bge-small / MiniLM, 384-d) for `lyr_full`, `lyr_open`, and `lyr_close` (first/last ~4 lines via synced timestamps). Theme/sentiment tags come from a small fixed taxonomy (heartbreak, defiance, celebration, longing, …) via a cheap LLM classification (Claude Haiku 4.5) batched once per track. Instrumentals get `lyr_* = NULL`, and the lyric term then drops out of the cost.

**Analyzer CLI**
- `griot scan ~/Music` — find files, hash, fingerprint, resolve MBIDs, show the popularity gate
- `griot analyze` — resumable job queue in local SQLite, overnight batch, progress bar
- `griot submit` — POST features only (signed with a per-user token); audio never leaves the machine
- later: `griot render-mix <bridge_id>` — builds an actual beat-matched, crossfaded mix from the user's own files, using the stored downbeats/outro points (a strong portfolio demo)

**Catalog integrity:** multiple submissions per MBID are merged by median, with outlier rejection (e.g. BPM octave errors, wrong-version masters detected by a duration mismatch over 5s against MusicBrainz).

**Licensing note:** MuQ weights and Essentia TF models are non-commercial (CC BY-NC / BY-NC-SA), and the Essentia library is AGPL. That's fine for a portfolio. Commercializing would require swapping models (e.g. LAION-CLAP) or getting licenses. Keep model choice behind an interface in `griot_core.embed`.

---

## 4. Bridge engine (core algorithm)

**Transition cost** `c(A→B)`, each term normalized to [0,1], weights in a config:
- `w_snd · (1 − cos(emb_outro_A, emb_intro_B))` — sonic continuity at the seam
- `w_bpm · tempo_penalty(A,B)` — log-ratio, tolerant of half/double time, steeper beyond ±6%
- `w_key · camelot_distance(A,B)` — 0 same, small for ±1 / relative major-minor, large otherwise
- `w_nrg · |energy_end_A − energy_start_B|` and the same for valence/arousal (from trajectories)
- `w_lyr · (1 − cos(lyr_close_A, lyr_open_B))` + theme-jump penalty (skipped if either is instrumental)
- `w_pop · |log pop_A − log pop_B|`, plus hard constraints: no repeat track, max 1 per artist per bridge (configurable), filters such as explicit content, year range, tier

**Progress term** (keeps the bridge *gradual* rather than drifting through hubs): for step *i* of *N*, target point `t_i = slerp(emb_full_S, emb_full_T, i/(N+1))`. Each step pays `w_prog · (1 − cos(emb_full_X, t_i))`. In **arc mode**, the user's drawn curve also sets target energy/valence per step.

**Search: layered Viterbi**
1. For each step *i*, query pgvector HNSW for ~200 candidates nearest `t_i` (after filters) → layer L_i.
2. DP over layers: `best[i][x] = min_y best[i−1][y] + c(y→x) + prog(x,i)`. That's 15×200×200 = 600k cost evaluations, vectorized in NumPy, well under 1s.
3. Handle no-repeat and artist constraints with a k-best/beam variant (keep the top-k partial paths per node) and a re-rank.
4. **Multiple waypoints:** solve each consecutive pair, with length allocated in proportion to the embedding distance (or set by the user).
5. **Text steering:** an optional prompt ("make it moodier") is embedded with MuQ-MuLan's text tower and added as an extra pull term on `t_i`.

Output per bridge: the ordered tracks, the cost of each transition broken down by term (shown in the UI as "why this track"), and a confidence score based on tier coverage.

**Tuning loop:** start with hand-set weights and tune on tier A-open with the eval metrics (§7). Then log per-transition 👍/👎 from users and fit the weights (logistic regression / pairwise ranking on the per-term features). Stretch goal: a learned transition model trained on real DJ-mix transitions (academic DJ-mix datasets) as a prior.

---

## 5. Database (Postgres + pgvector)

Tables: `recordings` (mbid PK, isrc, title, artist_credit, duration, year, explicit, popularity cols, tier, analyzer_version) · `artists` · `features_global` (bpm, key, mode, camelot, lufs, danceability, valence, arousal, tags jsonb) · `structure` (beats/downbeats/segments jsonb) · `trajectories` (float16 arrays) · `embeddings` (`emb_full|intro|outro|chorus vector(512)`, `lyr_full|open|close vector(384)`) · `submissions` (raw per-user analyses, for merging) · `external_ids` (spotify/apple/deezer ids) · `bridges` + `bridge_feedback`.

HNSW indexes on `emb_full` and `emb_intro`. Plain pgvector HNSW is enough at MVP scale (≤ a few million rows); pgvectorscale/DiskANN is only worth it once memory becomes a problem.

---

## 6. API + Web app

**FastAPI:** `POST /submissions` (analyzer ingest) · `GET /search?q=` (catalog typeahead) · `POST /bridges` (waypoints[], length, arc?, prompt?, filters) → bridge + per-term costs · `POST /bridges/{id}/feedback` · `POST /export/{spotify|apple|m3u}`.

**Next.js:**
- Waypoint picker with search; length/strictness/deep-cuts sliders
- Bridge view: track list plus a **D3 trajectory chart** (BPM, energy, valence across the whole album, using each song's *internal* curve rather than one dot per song), a Camelot wheel path, and a "why" tooltip per transition
- Audition: Deezer 30s previews, cued to each song's outro → next song's intro where possible
- Arc mode (phase 2): draw an energy/valence curve on a canvas
- Export: **Spotify** (OAuth, dev-mode demo users), **Apple Music** (MusicKit; needs the $99/yr developer program; add songs to the library, then the playlist), **M3U** (analyzer users can play from their own files)

---

## 7. Evaluation (what makes this credible as a portfolio piece)

- **Offline metrics:** mean and max transition cost; BPM/key violations per bridge; progress monotonicity (Spearman of step index vs. similarity to T); artist and genre diversity; coverage.
- **Baselines:** random walk between endpoints; straight-line nearest-neighbor using `emb_full` only (no seam awareness); Boil-the-Frog-style artist-graph shortest path; and Griot with **preview-only** features. The last ablation directly tests the "full songs matter" thesis.
- **Listening test:** blind pairwise A/B with 10–20 people (Griot vs. baselines) via a simple page in the app. Report win rates.

---

## 8. Milestones — fast track (~6 weeks, depth kept, scope sequenced)

Speed comes from three rules, not from cutting depth:
1. **De-risk first.** On day 1, spike the fragile model installs (allin1/natten, essentia-tensorflow, MuQ) so the critical path never stalls.
2. **Overlap long-running compute with coding.** Corpus analysis on Modal and your library's overnight analyzer runs happen *while* the engine and app get built.
3. **Contract-first.** The `TrackFeatures` schema and the `/bridges` API contract are frozen in week 1, so the engine, API, and web app can each be built against fixtures without waiting on real data.

Everything that makes Griot *Griot* stays in the MVP: full-song structure, outro↔intro seam embeddings, trajectories, lyrics, Viterbi pathfinding, and the eval with the preview-only ablation. Only peripheral features move later.

| Week | Deliverable | Exit criteria |
|---|---|---|
| **1** | M0 scaffold (uv monorepo, Docker pgvector, CI, schema + API contract frozen, fixtures) · **model install spike** on 20 local files · analyzer `scan` + fingerprint/MBID | Tests green; per-track timing known; 20 files → valid `TrackFeatures` JSON |
| **2** | Analyzer v1 complete (allin1 + Essentia + MuQ-MuLan, resumable queue) → **start overnight run on your library** · Modal job → **launch FMA/MTG-Jamendo backfill in background** · popularity (Last.fm + ListenBrainz) + LRCLIB lyrics/embeddings/theme tags | Library run in progress; ≥50k open tracks queued; popular MBID set built |
| **3** | **Bridge engine**: cost fn, Viterbi + constraints, waypoints, arc targets (engine side), text steering · eval harness + all baselines incl. preview-only ablation | Beats NN-only baseline offline; `griot bridge "A" "B" -n 12` works on real data |
| **4** | FastAPI (`/submissions` + merge, `/search`, `/bridges`, feedback) · web app skeleton against fixtures → real API | End-to-end: analyze → submit → bridge in browser |
| **5** | Web depth: D3 within-song trajectories, Camelot path, "why" tooltips, Deezer previews cued at seams · Spotify + M3U export · weights tuned on eval | Polished demo usable by 5 Spotify test users |
| **6** | Blind listening test (10–20 people), weight fit from feedback, deploy (single small VM or Fly + Neon), write-up with results | Live demo + published eval numbers |
| Later | Arc-drawing UI, AcousticBrainz tier-C import (large dump; it adds coverage, not depth), Apple Music export, `render-mix`, packaged analyzer, learned transition model | — |

**Costs (MVP):** about $0–50/month. Local dev is free (Docker). Modal free credits cover corpus batch jobs. Managed Postgres free/hobby tier or a $5–10 VM. API hosting is free–$20. Apple developer program $99/yr is optional. The LLM theme-tagging is a one-off batch costing a few dollars per 100k tracks.

---

## 9. First execution steps after approval
1. Write this plan to `docs/ROADMAP.md`, add `.gitignore` (incl. `.DS_Store`), and commit.
2. Do M0 scaffold: uv workspace, `packages/griot_core` with the pydantic `TrackFeatures` schema + `camelot.py` + tests, `docker-compose.yml` with pgvector, and an alembic baseline migration.
3. Freeze the `TrackFeatures` schema + `/bridges` request/response contract with JSON fixtures.
4. Spike the model risk first: run allin1 + MuQ-MuLan + Essentia on ~20 local files on your Mac to measure per-track time and check that the models install cleanly (allin1/natten and essentia-tensorflow are the fragile dependencies on macOS; fallback is a Linux Docker image or Modal).

## Verification
- `pytest` for `griot_core` (Camelot distances, tempo penalty incl. half/double time, Viterbi on a toy graph with a known optimum, constraint handling).
- Analyzer golden tests: a few CC-licensed FMA tracks with hand-checked BPM/key/segment boundaries.
- End-to-end smoke test: `docker compose up` → `griot analyze` on a sample folder → `griot submit` → `POST /bridges` returns N tracks with per-term costs → web UI renders the bridge and plays previews.
- Eval report from `eval/` comparing Griot against the baselines (including the preview-only ablation).
