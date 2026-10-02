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

    Preview-only (tier B) analyses are used only until a full-song analysis arrives.

    Submissions whose duration disagrees with the majority by > 5 s are treated as a
    different master (radio edit, live take) and dropped.
    """
    full = [d for d in docs if d.analyzer.full_audio]
    docs = full or docs  # a full-song analysis always outranks preview-only ones
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


def carry_enrichment(new: TrackFeatures, old: TrackFeatures | None) -> TrackFeatures:
    """Keep server-side enrichment (lyrics, popularity, external ids) across re-merges."""
    if old is None:
        return new
    new.lyrics = new.lyrics or old.lyrics
    if new.analyzer.full_audio and new.tier == "B":
        new.tier = "A"
    new.external_ids = old.external_ids | new.external_ids
    for k, v in old.popularity.model_dump().items():
        if getattr(new.popularity, k) is None:
            setattr(new.popularity, k, v)
    new.isrc = new.isrc or old.isrc
    new.mbid = new.mbid or old.mbid
    new.explicit = new.explicit if new.explicit is not None else old.explicit
    return new
