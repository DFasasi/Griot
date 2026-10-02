"""Merge several users' analyses of the same recording into one canonical document."""

from __future__ import annotations

import statistics
from collections import Counter

import numpy as np

from griot_core.schema import TrackFeatures

DURATION_TOLERANCE_S = 5.0


def _fold_bpm(values: list[float]) -> float:
    """Median tempo after folding half/double-time readings onto the majority octave."""
    ref = statistics.median(values)
    folded = []
    for v in values:
        cands = (v, v * 2, v / 2)
        folded.append(min(cands, key=lambda c: abs(np.log(c / ref))))
    return float(statistics.median(folded))


def merge(docs: list[TrackFeatures]) -> TrackFeatures:
    """Canonical doc: the analysis closest to the consensus, with medianed global numbers.

    Submissions whose duration disagrees with the majority by > 5 s are treated as a
    different master (radio edit, live take) and dropped.
    """
    if len(docs) == 1:
        return docs[0]
    dur = statistics.median(d.duration_s for d in docs)
    docs = [d for d in docs if abs(d.duration_s - dur) <= DURATION_TOLERANCE_S] or docs

    full = np.array([d.embeddings.full for d in docs])
    centroid = full.mean(0)
    base = docs[int(np.argmax(full @ centroid))].model_copy(deep=True)

    g = base.global_
    g.bpm = round(_fold_bpm([d.global_.bpm for d in docs]), 2)
    (key, mode), _ = Counter((d.global_.key, d.global_.mode) for d in docs).most_common(1)[0]
    if (key, mode) != (g.key, g.mode):
        donor = next(d for d in docs if (d.global_.key, d.global_.mode) == (key, mode))
        g.key, g.mode, g.camelot = key, mode, donor.global_.camelot
    g.lufs = round(statistics.median(d.global_.lufs for d in docs), 2)
    for attr in ("valence", "arousal", "danceability"):
        vals = [getattr(d.global_, attr) for d in docs if getattr(d.global_, attr) is not None]
        if vals:
            setattr(g, attr, round(statistics.median(vals), 3))
    return base
