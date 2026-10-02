"""Source-quality signals for an analysed file.

Wrong versions and lossy re-encodes (typical of video rips) corrupt exactly the seam data
Griot depends on, so each analysis carries these and the catalog can down-weight them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

LOW_BITRATE_KBPS = 160
TRANSCODE_CUTOFF_KHZ = 16.5  # lossy encoders at low bitrates low-pass around 15–16 kHz


def spectral_cutoff_khz(path: Path, seconds: float = 30.0) -> float | None:
    """Highest frequency that still carries meaningful energy in a mid-song excerpt."""
    import librosa

    try:
        dur = librosa.get_duration(path=str(path))
        y, sr = librosa.load(
            str(path), sr=44100, mono=True, offset=max(0.0, dur / 2 - seconds / 2), duration=seconds
        )
    except Exception:
        return None
    if len(y) < 4096:
        return None
    spec = np.abs(librosa.stft(y, n_fft=4096, hop_length=2048)) ** 2
    power_db = 10 * np.log10(spec.mean(axis=1) + 1e-12)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    floor = power_db.max() - 70  # 70 dB below the loudest band
    above = np.flatnonzero(power_db > floor)
    return round(float(freqs[above[-1]]) / 1000, 1) if len(above) else None


def bitrate_kbps(path: Path) -> int | None:
    import mutagen

    try:
        f = mutagen.File(path)
        br = getattr(f.info, "bitrate", 0) if f is not None else 0
        return int(br / 1000) if br else None
    except Exception:
        return None


def assess(path: Path, desc: dict, struct: dict) -> dict:
    br = bitrate_kbps(path)
    cutoff = spectral_cutoff_khz(path)
    flags = []
    if br and br < LOW_BITRATE_KBPS and path.suffix.lower() not in {".flac", ".wav", ".aiff", ".aif"}:
        flags.append("low_bitrate")
    if cutoff and cutoff < TRANSCODE_CUTOFF_KHZ:
        flags.append("lossy_transcode")
    labels = [s["label"] for s in struct.get("segments", [])]
    if labels and labels[0] not in {"start", "intro"} and desc["duration"] > 360:
        flags.append("possible_extended_or_video_cut")
    return {"bitrate_kbps": br, "cutoff_khz": cutoff, "flags": ",".join(flags) or "ok"}
