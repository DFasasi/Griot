"""Frozen data contracts shared by the analyzer, pipelines, API and web app.

`TrackFeatures` is what the analyzer submits (features only, never audio).
`BridgeRequest` / `BridgeResponse` is the public bridge API contract.
Bump SCHEMA_VERSION on any breaking change.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1"

Tier = Literal["A", "A-open", "B", "C"]
Mode = Literal["major", "minor"]
PitchClass = Literal["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


class AnalyzerInfo(BaseModel):
    version: str
    models: dict[str, str] = Field(default_factory=dict)  # model name -> weights id/hash
    full_audio: bool = True  # False when only a preview/snippet was analyzed


class GlobalFeatures(BaseModel):
    bpm: float = Field(gt=0)
    key: PitchClass
    mode: Mode
    key_strength: float = Field(ge=0, le=1, default=1.0)
    camelot: str  # e.g. "8A"
    lufs: float  # integrated loudness
    danceability: float | None = None
    valence: float | None = Field(default=None, ge=0, le=1)
    arousal: float | None = Field(default=None, ge=0, le=1)
    tags: dict[str, float] = Field(default_factory=dict)  # genre/mood/instrument probabilities


class Segment(BaseModel):
    start: float
    end: float
    label: str  # intro | verse | chorus | bridge | inst | solo | break | outro | start | end


class Structure(BaseModel):
    beats: list[float] = Field(default_factory=list)
    downbeats: list[float] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)


class Trajectory(BaseModel):
    """Fixed-hop time series over the whole song."""

    hop_s: float = 2.0
    energy: list[float]  # short-term loudness, LUFS
    onset_density: list[float] = Field(default_factory=list)  # onsets per second
    brightness: list[float] = Field(default_factory=list)  # spectral centroid, Hz
    chroma: list[list[float]] = Field(default_factory=list)  # n_frames x 12
    valence: list[float] = Field(default_factory=list)  # coarser windows, resampled to hop
    arousal: list[float] = Field(default_factory=list)


class Embeddings(BaseModel):
    model: str
    full: list[float]
    intro: list[float]
    outro: list[float]
    chorus: list[float] | None = None

    @model_validator(mode="after")
    def _same_dim(self) -> Embeddings:
        dims = {len(self.full), len(self.intro), len(self.outro)}
        if self.chorus is not None:
            dims.add(len(self.chorus))
        if len(dims) != 1:
            raise ValueError(f"embedding dims differ: {dims}")
        return self


class LyricFeatures(BaseModel):
    model: str
    instrumental: bool = False
    full: list[float] | None = None
    open: list[float] | None = None  # first lines
    close: list[float] | None = None  # last lines
    themes: list[str] = Field(default_factory=list)


class Popularity(BaseModel):
    lastfm_playcount: int | None = None
    lb_listens: int | None = None
    deezer_rank: int | None = None


class TrackFeatures(BaseModel):
    schema_version: str = SCHEMA_VERSION
    id: str  # MusicBrainz recording MBID when known, else "local:<sha1>" / "fma:<id>"
    mbid: str | None = None
    isrc: str | None = None
    title: str
    artist: str
    artist_ids: list[str] = Field(default_factory=list)
    duration_s: float = Field(gt=0)
    year: int | None = None
    explicit: bool | None = None
    tier: Tier = "A"
    analyzer: AnalyzerInfo
    global_: GlobalFeatures = Field(alias="global")
    structure: Structure = Field(default_factory=Structure)
    trajectory: Trajectory
    embeddings: Embeddings
    lyrics: LyricFeatures | None = None
    popularity: Popularity = Field(default_factory=Popularity)
    external_ids: dict[str, str] = Field(default_factory=dict)  # spotify/deezer/apple ids

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------- bridge API contract


class ArcPoint(BaseModel):
    t: float = Field(ge=0, le=1)  # position along the whole bridge
    energy: float | None = Field(default=None, ge=0, le=1)
    valence: float | None = Field(default=None, ge=0, le=1)


class BridgeFilters(BaseModel):
    min_playcount: int | None = 5000
    max_per_artist: int = 1
    allow_explicit: bool = True
    tiers: list[Tier] = Field(default_factory=lambda: ["A", "A-open", "B"])
    year_min: int | None = None
    year_max: int | None = None


class BridgeRequest(BaseModel):
    waypoints: list[str] = Field(min_length=2)  # track ids, in order
    length: int = Field(default=12, ge=1, le=60)  # bridge tracks in total (excl. waypoints)
    gap_lengths: list[int] | None = None  # optional explicit per-gap lengths
    arc: list[ArcPoint] | None = None
    prompt: str | None = None  # text steering, e.g. "moodier, more acoustic"
    filters: BridgeFilters = Field(default_factory=BridgeFilters)
    weights: dict[str, float] | None = None  # override cost weights

    @model_validator(mode="after")
    def _gaps(self) -> BridgeRequest:
        if self.gap_lengths is not None and len(self.gap_lengths) != len(self.waypoints) - 1:
            raise ValueError("gap_lengths must have len(waypoints) - 1 entries")
        return self


class BridgeTrack(BaseModel):
    id: str
    title: str
    artist: str
    role: Literal["waypoint", "bridge"]
    bpm: float
    camelot: str
    energy: float  # normalized 0..1
    valence: float | None = None
    tier: Tier
    preview_url: str | None = None


class Transition(BaseModel):
    from_id: str
    to_id: str
    cost: float
    terms: dict[str, float]  # per-term weighted contribution, for "why this track"


class BridgeResponse(BaseModel):
    id: str | None = None
    tracks: list[BridgeTrack]
    transitions: list[Transition]
    total_cost: float
    confidence: float  # share of full-song-analyzed tracks
    weights: dict[str, float]


# ---------------------------------------------------------------- library import / cross-platform mapping

Source = Literal["spotify", "youtube", "file", "manual"]
Coverage = Literal["full", "preview", "missing", "unmatched"]


class ImportItem(BaseModel):
    """A song as some platform describes it. ISRC is often absent (Spotify dropped it from
    dev-mode track objects in Feb 2026; YouTube never had it), so title/artist/duration matter."""

    source: Source
    source_id: str | None = None  # spotify track id, youtube video id, file sha1
    title: str
    artist: str | None = None
    album: str | None = None
    duration_s: float | None = None
    isrc: str | None = None


class Resolution(BaseModel):
    item: ImportItem
    status: Coverage
    track_id: str | None = None  # Griot catalog id when the song is in the catalog
    title: str | None = None  # canonical title/artist after matching
    artist: str | None = None
    isrc: str | None = None
    deezer_id: str | None = None
    preview_url: str | None = None
    confidence: float = 0.0  # 0..1 match confidence
