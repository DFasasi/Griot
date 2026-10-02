"""Synthetic but structurally realistic TrackFeatures for tests, fixtures and UI dev.

Tracks live on a smooth latent "style" manifold: embedding, tempo, key, loudness and
mood all drift with the latent position, so nearby tracks are genuinely compatible and
a good bridge exists between any two points.
"""

from __future__ import annotations

import numpy as np

from griot_core.schema import (
    AnalyzerInfo,
    Embeddings,
    GlobalFeatures,
    LyricFeatures,
    Popularity,
    Segment,
    Structure,
    TrackFeatures,
    Trajectory,
)
from griot_core.theory import PITCH_CLASSES, camelot

_SYLL = ["ka", "lo", "mi", "ra", "ve", "su", "do", "ny", "ze", "ba", "ti", "qu", "fe", "ol"]
_THEMES = ["heartbreak", "longing", "celebration", "defiance", "nostalgia", "desire", "faith"]


def _name(rng: np.random.Generator, parts: int) -> str:
    return "".join(rng.choice(_SYLL, size=parts)).capitalize()


def make_tracks(
    n: int = 2000,
    dim: int = 64,
    seed: int = 0,
    n_artists: int | None = None,
    lyr_dim: int = 32,
) -> list[TrackFeatures]:
    rng = np.random.default_rng(seed)
    n_artists = n_artists or max(2, n // 4)
    proj = rng.normal(size=(3, dim))
    lproj = rng.normal(size=(3, lyr_dim))
    artists = [f"{_name(rng, 2)} {_name(rng, 2)}" for _ in range(n_artists)]
    artist_pos = rng.uniform(-1, 1, size=(n_artists, 3))

    tracks = []
    for i in range(n):
        a = int(rng.integers(n_artists))
        z = artist_pos[a] + rng.normal(scale=0.15, size=3)  # latent style
        base = np.tanh(z @ proj) + rng.normal(scale=0.05, size=dim)
        intro = base + rng.normal(scale=0.15, size=dim) + 0.2 * np.tanh(z[0] - 0.3)
        outro = base + rng.normal(scale=0.15, size=dim) + 0.2 * np.tanh(z[0] + 0.3)

        bpm = float(np.clip(110 + 35 * z[0] + rng.normal(scale=4), 60, 180))
        pc_major = int(round((z[1] + 1) * 5.5 + rng.normal(scale=0.6))) % 12  # walks the wheel
        mode = "minor" if z[2] + rng.normal(scale=0.3) < 0 else "major"
        key_pc = pc_major if mode == "major" else (pc_major - 3) % 12
        key = PITCH_CLASSES[key_pc]
        lufs = float(np.clip(-12 + 5 * z[0] + rng.normal(scale=1.5), -24, -4))
        valence = float(np.clip(0.5 + 0.35 * z[2] + rng.normal(scale=0.08), 0, 1))
        arousal = float(np.clip(0.5 + 0.35 * z[0] + rng.normal(scale=0.08), 0, 1))
        dur = float(rng.uniform(150, 300))
        frames = int(dur / 2)
        shape = np.sin(np.linspace(0, np.pi, frames)) ** 0.5  # quiet intro/outro, loud middle
        energy = (lufs - 8 + 10 * shape + rng.normal(scale=0.5, size=frames)).tolist()

        instrumental = rng.random() < 0.15
        lyr = None if instrumental else np.tanh(z @ lproj) + rng.normal(scale=0.2, size=lyr_dim)
        tracks.append(
            TrackFeatures(
                id=f"syn:{seed}:{i}",
                title=f"{_name(rng, 2)} {_name(rng, 1)}",
                artist=artists[a],
                artist_ids=[f"syn-artist:{seed}:{a}"],
                duration_s=dur,
                year=int(rng.integers(1965, 2026)),
                tier="A-open",
                analyzer=AnalyzerInfo(version="synthetic", models={"embed": "synthetic"}),
                global_=GlobalFeatures(
                    bpm=bpm,
                    key=key,
                    mode=mode,
                    key_strength=float(rng.uniform(0.5, 1.0)),
                    camelot=camelot(key, mode),
                    lufs=lufs,
                    danceability=float(np.clip(0.5 + 0.3 * z[0], 0, 1)),
                    valence=valence,
                    arousal=arousal,
                ),
                structure=Structure(
                    beats=[],
                    downbeats=[],
                    segments=[
                        Segment(start=0, end=16, label="intro"),
                        Segment(start=16, end=dur - 20, label="verse"),
                        Segment(start=dur - 20, end=dur, label="outro"),
                    ],
                ),
                trajectory=Trajectory(hop_s=2.0, energy=energy),
                embeddings=Embeddings(
                    model="synthetic", full=base.tolist(), intro=intro.tolist(), outro=outro.tolist()
                ),
                lyrics=LyricFeatures(
                    model="synthetic",
                    instrumental=instrumental,
                    open=None if lyr is None else (lyr + rng.normal(scale=0.1, size=lyr_dim)).tolist(),
                    close=None if lyr is None else (lyr + rng.normal(scale=0.1, size=lyr_dim)).tolist(),
                    themes=[] if lyr is None else [str(rng.choice(_THEMES))],
                ),
                popularity=Popularity(lastfm_playcount=int(10 ** rng.uniform(3, 7))),
            )
        )
    return tracks
