"""Full-song feature extraction: audio file -> TrackFeatures (minus identity/popularity).

Stages (see docs/spike-results.md for timings):
  1. structure  — all-in-one (beats, downbeats, functional segments)
  2. descriptors — Essentia (key, tempo, loudness, danceability, per-frame trajectories)
  3. embeddings  — MuQ-MuLan over 10 s windows, pooled into full / intro / outro / chorus,
                   plus zero-shot valence/arousal/tags from the joint music–text space
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np

from griot_core.schema import (
    AnalyzerInfo,
    Embeddings,
    GlobalFeatures,
    Segment,
    Structure,
    Trajectory,
)
from griot_core.theory import PITCH_CLASSES, camelot, pitch_class

ANALYZER_VERSION = "0.1.0"
MULAN_ID = "OpenMuQ/MuQ-MuLan-large"
MULAN_SR = 24000
WINDOW_S = 10.0
HOP_S = 2.0  # trajectory resolution
EDGE_S = 30.0  # default intro/outro span when segments are missing or implausible

# Zero-shot anchors in MuQ-MuLan's joint space. Score = sim(pos) - sim(neg), squashed to 0..1.
VALENCE_ANCHORS = (
    ["happy cheerful uplifting music", "joyful bright positive song"],
    ["sad melancholic music", "dark gloomy depressing song"],
)
AROUSAL_ANCHORS = (
    ["energetic intense loud music", "fast powerful aggressive song"],
    ["calm relaxed quiet music", "slow soft gentle song"],
)
TAGS = [
    "pop", "rock", "hip hop", "rap", "r&b", "soul", "funk", "jazz", "blues", "electronic",
    "house", "techno", "drum and bass", "ambient", "classical", "country", "folk", "reggae",
    "afrobeats", "latin", "metal", "punk", "indie", "gospel", "lo-fi", "acoustic", "piano",
    "guitar", "synth", "orchestral", "vocal", "instrumental", "dance", "chill", "romantic",
]  # fmt: skip
ANCHOR_SCALE = 8.0  # logistic sharpness for zero-shot scores (cosine deltas are ~±0.2)


@dataclass
class Models:
    """Lazily loaded, process-wide model handles."""

    device: str = field(default_factory=lambda: _device())

    @cached_property
    def mulan(self):
        import torch
        from muq import MuQMuLan

        torch.set_grad_enabled(False)
        return MuQMuLan.from_pretrained(MULAN_ID).to(self.device).eval()

    @cached_property
    def text_anchors(self) -> dict[str, np.ndarray]:
        def emb(texts: list[str]) -> np.ndarray:
            e = self.mulan(texts=texts).cpu().numpy()
            return _unit(e)

        return {
            "val_pos": emb(VALENCE_ANCHORS[0]).mean(0),
            "val_neg": emb(VALENCE_ANCHORS[1]).mean(0),
            "aro_pos": emb(AROUSAL_ANCHORS[0]).mean(0),
            "aro_neg": emb(AROUSAL_ANCHORS[1]).mean(0),
            "tags": emb([f"{t} music" for t in TAGS]),
        }

    def embed_text(self, text: str) -> np.ndarray:
        return _unit(self.mulan(texts=[text]).cpu().numpy())[0]


def _device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _unit(m: np.ndarray) -> np.ndarray:
    return m / np.maximum(np.linalg.norm(m, axis=-1, keepdims=True), 1e-9)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


# --------------------------------------------------------------------------- structure


def analyze_structure(paths: list[Path], work_dir: Path) -> dict[Path, dict]:
    """Batch structure analysis; the model is loaded once per call."""
    work_dir.mkdir(parents=True, exist_ok=True)
    kw = dict(
        out_dir=work_dir / "struct",
        demix_dir=work_dir / "demix",
        spec_dir=work_dir / "spec",
        overwrite=True,
    )
    if sys.platform == "darwin":
        from allin1_mlx import analyze

        results = analyze([str(p) for p in paths], multiprocess=False, **kw)
    else:
        from allin1_infer import analyze

        results = analyze([str(p) for p in paths], **kw)
    if not isinstance(results, list):
        results = [results]
    out = {}
    for p, r in zip(paths, results, strict=True):
        out[p] = {
            "bpm": float(r.bpm) if r.bpm else None,
            "beats": [round(float(b), 3) for b in r.beats],
            "downbeats": [round(float(b), 3) for b in r.downbeats],
            "segments": [
                {"start": float(s.start), "end": float(s.end), "label": s.label} for s in r.segments
            ],
        }
    return out


# --------------------------------------------------------------------------- descriptors


def _frames_to_hop(values: np.ndarray, frame_hop: float, n_out: int, reduce=np.mean) -> np.ndarray:
    """Aggregate a per-frame series (frame_hop seconds) into HOP_S buckets."""
    per = max(1, int(round(HOP_S / frame_hop)))
    out = []
    for i in range(n_out):
        chunk = values[i * per : (i + 1) * per]
        out.append(reduce(chunk, axis=0) if len(chunk) else (out[-1] if out else values[0] * 0))
    return np.asarray(out)


def analyze_descriptors(path: Path) -> dict:
    import essentia.standard as es

    stereo, sr, *_ = es.AudioLoader(filename=str(path))()
    if stereo.shape[1] == 1:
        stereo = np.repeat(stereo, 2, axis=1)
    mono = es.MonoMixer()(stereo, 2)
    if sr != 44100:
        mono = es.Resample(inputSampleRate=sr, outputSampleRate=44100)(mono)
        sr_mono = 44100
    else:
        sr_mono = sr
    duration = len(mono) / sr_mono
    n_out = max(1, int(np.ceil(duration / HOP_S)))

    key, scale, strength = es.KeyExtractor(profileType="edma", sampleRate=sr_mono)(mono)
    bpm, beats, *_ = es.RhythmExtractor2013(method="multifeature")(mono)
    _, short_term, integrated, lra = es.LoudnessEBUR128(sampleRate=float(sr))(stereo)
    dance, _ = es.Danceability(sampleRate=sr_mono)(mono)

    # Per-frame spectral descriptors.
    frame, hop = 4096, 2048
    w = es.Windowing(type="blackmanharris62")
    spec = es.Spectrum()
    peaks = es.SpectralPeaks(orderBy="magnitude", magnitudeThreshold=1e-5, minFrequency=40,
                             maxFrequency=5000, maxPeaks=60, sampleRate=sr_mono)  # fmt: skip
    hpcp = es.HPCP(size=12, referenceFrequency=440, sampleRate=sr_mono, normalized="unitMax")
    centroid = es.Centroid(range=sr_mono / 2)
    chroma, bright = [], []
    for fr in es.FrameGenerator(mono, frameSize=frame, hopSize=hop, startFromZero=True):
        s = spec(w(fr))
        f, m = peaks(s)
        chroma.append(hpcp(f, m))
        bright.append(centroid(s))
    frame_hop = hop / sr_mono
    chroma_h = _frames_to_hop(np.asarray(chroma), frame_hop, n_out)
    bright_h = _frames_to_hop(np.asarray(bright), frame_hop, n_out)
    # Reorder HPCP (A-based) to C-based pitch classes.
    chroma_h = np.roll(chroma_h, 3, axis=1)

    onsets = es.OnsetRate()(mono)[0]
    onset_density = np.histogram(onsets, bins=n_out, range=(0, n_out * HOP_S))[0] / HOP_S
    energy = _frames_to_hop(np.asarray(short_term), 0.1, n_out)

    return {
        "duration": duration,
        "key": key,
        "mode": "major" if scale == "major" else "minor",
        "key_strength": float(np.clip(strength, 0, 1)),
        "bpm": float(bpm),
        "lufs": float(integrated),
        "loudness_range": float(lra),
        "danceability": float(np.clip(dance / 3.0, 0, 1)),
        "energy": np.round(energy, 2).tolist(),
        "onset_density": np.round(onset_density, 2).tolist(),
        "brightness": np.round(bright_h * sr_mono / 2, 1).tolist(),
        "chroma": np.round(chroma_h, 3).tolist(),
    }


# --------------------------------------------------------------------------- embeddings


def window_embeddings(path: Path, models: Models, batch: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """MuQ-MuLan embeddings for consecutive 10 s windows. Returns (centres_s, (W, 512))."""
    import librosa
    import torch

    wav, _ = librosa.load(str(path), sr=MULAN_SR, mono=True)
    win = int(WINDOW_S * MULAN_SR)
    if len(wav) < win:
        wav = np.pad(wav, (0, win - len(wav)))
    starts = list(range(0, len(wav) - win + 1, win))
    if len(wav) - (starts[-1] + win) > win // 2:  # keep a final window flush with the end
        starts.append(len(wav) - win)
    chunks = np.stack([wav[s : s + win] for s in starts]).astype(np.float32)
    embs = []
    for i in range(0, len(chunks), batch):
        x = torch.from_numpy(chunks[i : i + batch]).to(models.device)
        embs.append(models.mulan(wavs=x).cpu().numpy())
    centres = (np.asarray(starts) + win / 2) / MULAN_SR
    return centres, _unit(np.concatenate(embs))


def _span(segments: list[dict], labels: set[str], from_start: bool, duration: float) -> tuple[float, float]:
    """Contiguous run of `labels` segments at the start/end of the song, clamped to sane bounds."""
    segs = segments if from_start else list(reversed(segments))
    span = None
    for s in segs:
        if s["label"] in {"start", "end"}:
            continue
        if s["label"] not in labels:
            break
        span = (s["start"], s["end"]) if span is None else (min(span[0], s["start"]), max(span[1], s["end"]))
    if span is None or not (8.0 <= span[1] - span[0] <= 60.0):
        return (0.0, EDGE_S) if from_start else (max(0.0, duration - EDGE_S), duration)
    return span


def pool_embeddings(centres: np.ndarray, embs: np.ndarray, segments: list[dict], duration: float) -> dict:
    def pool(lo: float, hi: float) -> np.ndarray:
        m = (centres >= lo) & (centres <= hi)
        if not m.any():
            m[np.argmin(np.abs(centres - (lo + hi) / 2))] = True
        return _unit(embs[m].mean(0))

    intro = _span(segments, {"intro"}, True, duration)
    outro = _span(segments, {"outro"}, False, duration)
    chorus_segs = [s for s in segments if s["label"] == "chorus"]
    chorus = None
    if chorus_segs:
        m = np.zeros(len(centres), bool)
        for s in chorus_segs:
            m |= (centres >= s["start"]) & (centres <= s["end"])
        if m.any():
            chorus = _unit(embs[m].mean(0))
    return {
        "full": _unit(embs.mean(0)),
        "intro": pool(*intro),
        "outro": pool(*outro),
        "chorus": chorus,
        "intro_span": intro,
        "outro_span": outro,
    }


def zero_shot(embs: np.ndarray, models: Models) -> dict:
    a = models.text_anchors
    val = _sigmoid(ANCHOR_SCALE * (embs @ a["val_pos"] - embs @ a["val_neg"]))
    aro = _sigmoid(ANCHOR_SCALE * (embs @ a["aro_pos"] - embs @ a["aro_neg"]))
    full = _unit(embs.mean(0))
    logits = 20.0 * (a["tags"] @ full)
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    top = np.argsort(-probs)[:8]
    return {
        "valence_windows": val,
        "arousal_windows": aro,
        "tags": {TAGS[i]: round(float(probs[i]), 3) for i in top},
    }


# --------------------------------------------------------------------------- assembly


def reconcile_bpm(structure_bpm: float | None, essentia_bpm: float) -> float:
    """Prefer the beat-tracker tempo; Essentia often lands on the double/half octave."""
    if not structure_bpm:
        return essentia_bpm
    return float(structure_bpm)


def build_features(path: Path, struct: dict, desc: dict, centres, embs, zs: dict) -> dict:
    """Combine stage outputs into the feature part of a TrackFeatures document."""
    duration = desc["duration"]
    pooled = pool_embeddings(centres, embs, struct["segments"], duration)
    n = len(desc["energy"])
    grid = np.arange(n) * HOP_S + HOP_S / 2
    val_traj = np.interp(grid, centres, zs["valence_windows"])
    aro_traj = np.interp(grid, centres, zs["arousal_windows"])
    bpm = reconcile_bpm(struct.get("bpm"), desc["bpm"])

    return {
        "duration_s": round(duration, 2),
        "analyzer": AnalyzerInfo(
            version=ANALYZER_VERSION,
            models={"embed": MULAN_ID, "structure": "all-in-one/harmonix-all", "descriptors": "essentia"},
            full_audio=True,
        ),
        "global_": GlobalFeatures(
            bpm=round(bpm, 2),
            key=PITCH_CLASSES[pitch_class(desc["key"])],
            mode=desc["mode"],
            key_strength=round(desc["key_strength"], 3),
            camelot=camelot(desc["key"], desc["mode"]),
            lufs=round(desc["lufs"], 2),
            danceability=round(desc["danceability"], 3),
            valence=round(float(zs["valence_windows"].mean()), 3),
            arousal=round(float(zs["arousal_windows"].mean()), 3),
            tags=zs["tags"],
        ),
        "structure": Structure(
            beats=struct["beats"],
            downbeats=struct["downbeats"],
            segments=[Segment(**s) for s in struct["segments"]],
        ),
        "trajectory": Trajectory(
            hop_s=HOP_S,
            energy=desc["energy"],
            onset_density=desc["onset_density"],
            brightness=desc["brightness"],
            chroma=desc["chroma"],
            valence=np.round(val_traj, 3).tolist(),
            arousal=np.round(aro_traj, 3).tolist(),
        ),
        "embeddings": Embeddings(
            model=MULAN_ID,
            full=np.round(pooled["full"], 5).tolist(),
            intro=np.round(pooled["intro"], 5).tolist(),
            outro=np.round(pooled["outro"], 5).tolist(),
            chorus=None if pooled["chorus"] is None else np.round(pooled["chorus"], 5).tolist(),
        ),
    }
