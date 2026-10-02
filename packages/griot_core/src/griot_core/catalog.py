"""Columnar, in-memory view of a set of tracks for vectorized cost evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from griot_core.schema import TrackFeatures
from griot_core.theory import parse_camelot

EDGE_SECONDS = 16.0  # how much of the start/end of a song counts as "the seam"
SILENCE_LUFS = -45.0


def norm_lufs(x: np.ndarray | float) -> np.ndarray | float:
    """Map loudness (LUFS) to a 0..1 energy scale; mastered music sits around -24..-4."""
    return np.clip((np.asarray(x, dtype=np.float64) + 24.0) / 20.0, 0.0, 1.0)


def _unit(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(n == 0, 1.0, n)


def _edges(series: list[float], hop: float, energy: list[float]) -> tuple[float, float]:
    """Mean of a series over the first/last EDGE_SECONDS of audible audio."""
    if not series:
        return np.nan, np.nan
    s = np.asarray(series, dtype=np.float64)
    e = np.asarray(energy[: len(s)], dtype=np.float64) if energy else np.zeros(len(s))
    audible = np.flatnonzero(e > SILENCE_LUFS) if len(e) == len(s) else np.arange(len(s))
    if len(audible) == 0:
        audible = np.arange(len(s))
    lo, hi = audible[0], audible[-1] + 1
    k = max(1, int(round(EDGE_SECONDS / hop)))
    return float(s[lo : lo + k].mean()), float(s[max(lo, hi - k) : hi].mean())


@dataclass
class Catalog:
    tracks: list[TrackFeatures]
    ids: list[str] = field(init=False)
    index: dict[str, int] = field(init=False)

    def __post_init__(self) -> None:
        t = self.tracks
        n = len(t)
        self.ids = [x.id for x in t]
        self.index = {x.id: i for i, x in enumerate(t)}
        self.artist_key = [(x.artist_ids[0] if x.artist_ids else x.artist.lower()) for x in t]

        self.full = _unit(np.array([x.embeddings.full for x in t], dtype=np.float32))
        self.intro = _unit(np.array([x.embeddings.intro for x in t], dtype=np.float32))
        self.outro = _unit(np.array([x.embeddings.outro for x in t], dtype=np.float32))

        self.bpm = np.array([x.global_.bpm for x in t])
        cam = [parse_camelot(x.global_.camelot) for x in t]
        self.cam_num = np.array([c[0] for c in cam])
        self.cam_letter = np.array([c[1] for c in cam])
        self.key_strength = np.array([x.global_.key_strength for x in t])

        self.e_start, self.e_end, self.e_mean = np.empty(n), np.empty(n), np.empty(n)
        self.v_start, self.v_end = np.full(n, np.nan), np.full(n, np.nan)
        self.a_start, self.a_end = np.full(n, np.nan), np.full(n, np.nan)
        self.valence = np.full(n, np.nan)
        for i, x in enumerate(t):
            tr = x.trajectory
            s, e = _edges(tr.energy, tr.hop_s, tr.energy)
            self.e_start[i], self.e_end[i] = norm_lufs(s), norm_lufs(e)
            self.e_mean[i] = norm_lufs(x.global_.lufs)
            if tr.valence:
                self.v_start[i], self.v_end[i] = _edges(tr.valence, tr.hop_s, tr.energy)
            elif x.global_.valence is not None:
                self.v_start[i] = self.v_end[i] = x.global_.valence
            if tr.arousal:
                self.a_start[i], self.a_end[i] = _edges(tr.arousal, tr.hop_s, tr.energy)
            elif x.global_.arousal is not None:
                self.a_start[i] = self.a_end[i] = x.global_.arousal
            if x.global_.valence is not None:
                self.valence[i] = x.global_.valence

        ldim = next((len(x.lyrics.open) for x in t if x.lyrics and x.lyrics.open), 1)
        self.has_lyr = np.array([bool(x.lyrics and x.lyrics.open and x.lyrics.close) for x in t], dtype=bool)
        self.lyr_open = np.zeros((n, ldim), dtype=np.float32)
        self.lyr_close = np.zeros((n, ldim), dtype=np.float32)
        for i, x in enumerate(t):
            if self.has_lyr[i]:
                self.lyr_open[i], self.lyr_close[i] = x.lyrics.open, x.lyrics.close
        self.lyr_open, self.lyr_close = _unit(self.lyr_open), _unit(self.lyr_close)

        pc = np.array([x.popularity.lastfm_playcount or x.popularity.lb_listens or 0 for x in t], dtype=float)
        self.playcount = pc
        self.log_pop = np.where(pc > 0, np.log10(np.maximum(pc, 1)), np.nan)
        self.full_audio = np.array([x.analyzer.full_audio for x in t], dtype=bool)
        self.tier = [x.tier for x in t]
        self.explicit = np.array([bool(x.explicit) for x in t], dtype=bool)
        self.year = np.array([x.year if x.year else -1 for x in t])

    def __len__(self) -> int:
        return len(self.tracks)
