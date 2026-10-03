"""Transition cost between tracks: every term normalized to [0, 1], then weighted.

The sonic term compares the *outro* of A with the *intro* of B — the seam a listener
actually hears — which is only possible with full-song analysis.
"""

from __future__ import annotations

import numpy as np

from griot_core.catalog import Catalog
from griot_core.theory import HALF_DOUBLE_EXTRA, TEMPO_MAX, TEMPO_SAFE

DEFAULT_WEIGHTS: dict[str, float] = {
    # transition terms
    "sound": 1.0,
    "tempo": 0.6,
    "key": 0.5,
    "energy": 0.5,
    "mood": 0.3,
    "lyrics": 0.25,
    "popularity": 0.15,
    # per-position terms (see pathfinder)
    "progress": 1.0,
    "arc": 0.6,
    "steer": 0.3,
    # path shape: a seam's cost counts as c + rough * c², so one jarring transition costs more
    # than two moderate ones (listeners notice the worst seam, not the average)
    "rough": 1.0,
}

LYRICS_NEUTRAL = 0.35  # used when either side has no lyrics, so instrumentals aren't favored
_CAMELOT_STEP = np.array([0.0, 0.15, 0.45, 0.7, 1.0, 1.0, 1.0, 1.0])


def merged_weights(overrides: dict[str, float] | None) -> dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    for k, v in (overrides or {}).items():
        if k not in w:
            raise KeyError(f"unknown weight {k!r}")
        w[k] = float(v)
    return w


def _tempo(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    best = np.full(np.broadcast(a, b).shape, np.inf)
    for factor, extra in ((1.0, 0.0), (2.0, HALF_DOUBLE_EXTRA), (0.5, HALF_DOUBLE_EXTRA)):
        x = np.abs(np.log(a / (b * factor)))
        p = np.where(
            x <= TEMPO_SAFE,
            0.3 * x / TEMPO_SAFE,
            0.3 + 0.7 * np.minimum(1.0, (x - TEMPO_SAFE) / (TEMPO_MAX - TEMPO_SAFE)),
        )
        best = np.minimum(best, np.minimum(1.0, p + extra))
    return best


def _camelot(cat: Catalog, f: np.ndarray, t: np.ndarray) -> np.ndarray:
    d = np.abs(cat.cam_num[f][:, None] - cat.cam_num[t][None, :])
    steps = np.minimum(d, 12 - d) + (cat.cam_letter[f][:, None] != cat.cam_letter[t][None, :])
    strength = np.minimum(cat.key_strength[f][:, None], cat.key_strength[t][None, :])
    return _CAMELOT_STEP[np.minimum(steps, 7)] * np.clip(strength, 0, 1)


def _absdiff(a: np.ndarray, b: np.ndarray, scale: float = 1.0) -> np.ndarray:
    d = np.abs(a[:, None] - b[None, :]) / scale
    return np.nan_to_num(np.clip(d, 0, 1), nan=0.0)


def transition_terms(cat: Catalog, f: np.ndarray, t: np.ndarray) -> dict[str, np.ndarray]:
    """Unweighted [0,1] terms for every pair (f[i] -> t[j]); each array has shape (len f, len t)."""
    f, t = np.asarray(f), np.asarray(t)
    sound = cat.sound_dist(cat.outro[f] @ cat.intro[t].T)
    tempo = _tempo(cat.bpm[f][:, None], cat.bpm[t][None, :])
    key = _camelot(cat, f, t)
    energy = _absdiff(cat.e_end[f], cat.e_start[t], scale=0.5)
    mood = 0.5 * (_absdiff(cat.v_end[f], cat.v_start[t], 0.5) + _absdiff(cat.a_end[f], cat.a_start[t], 0.5))
    both = cat.has_lyr[f][:, None] & cat.has_lyr[t][None, :]
    lyr = cat.lyr_dist(cat.lyr_close[f] @ cat.lyr_open[t].T)
    lyrics = np.where(both, lyr, LYRICS_NEUTRAL)
    popularity = _absdiff(cat.log_pop[f], cat.log_pop[t], scale=3.0)
    return {
        "sound": sound,
        "tempo": tempo,
        "key": key,
        "energy": energy,
        "mood": mood,
        "lyrics": lyrics,
        "popularity": popularity,
    }


TRANSITION_TERMS = ("sound", "tempo", "key", "energy", "mood", "lyrics", "popularity")


def transition_cost(cat: Catalog, f, t, weights: dict[str, float]) -> np.ndarray:
    terms = transition_terms(cat, f, t)
    return sum(weights[k] * terms[k] for k in TRANSITION_TERMS)


def explain(cat: Catalog, a: int, b: int, weights: dict[str, float]) -> dict[str, float]:
    terms = transition_terms(cat, np.array([a]), np.array([b]))
    return {k: round(float(weights[k] * terms[k][0, 0]), 4) for k in TRANSITION_TERMS}
