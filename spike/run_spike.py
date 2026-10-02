"""Day-1 risk spike: do allin1-mlx, Essentia and MuQ-MuLan run on this Mac, and how fast?"""

import json
import subprocess
import sys
import time
from pathlib import Path

import essentia.standard as es
import librosa
import numpy as np
import torch
from muq import MuQMuLan

AUDIO = sorted(Path(sys.argv[1] if len(sys.argv) > 1 else "spike/audio").glob("*.mp3"))
OUT = Path("spike/out")
OUT.mkdir(parents=True, exist_ok=True)
device = "mps" if torch.backends.mps.is_available() else "cpu"

t = time.time()
mulan = MuQMuLan.from_pretrained("OpenMuQ/MuQ-MuLan-large").to(device).eval()
print(f"MuQ-MuLan load: {time.time() - t:.1f}s on {device}")

rows = []
for path in AUDIO:
    r = {"file": path.name}

    t = time.time()
    subprocess.run(["spike/.venv/bin/allin1-mlx", str(path), "--out-dir", str(OUT / "struct")],
                   check=True, capture_output=True)
    r["t_structure"] = time.time() - t
    struct = json.loads((OUT / "struct" / f"{path.stem}.json").read_text())
    r["allin1_bpm"] = struct.get("bpm")
    r["segments"] = [s["label"] for s in struct.get("segments", [])]

    t = time.time()
    stereo, sr, *_ = es.AudioLoader(filename=str(path))()
    mono = es.MonoMixer()(stereo, stereo.shape[1])
    mono = es.Resample(inputSampleRate=sr, outputSampleRate=44100)(mono) if sr != 44100 else mono
    key, scale, strength = es.KeyExtractor(profileType="edma")(mono)
    bpm, *_ = es.RhythmExtractor2013(method="multifeature")(mono)
    _, short_term, integrated, _ = es.LoudnessEBUR128(sampleRate=sr)(stereo)
    dance, _ = es.Danceability()(mono)
    r["t_essentia"] = time.time() - t
    r.update(key=f"{key} {scale}", key_strength=round(float(strength), 2),
             essentia_bpm=round(float(bpm), 1), lufs=round(float(integrated), 1),
             danceability=round(float(dance), 2), st_loudness_frames=len(short_term))

    t = time.time()
    wav, _ = librosa.load(str(path), sr=24000, mono=True)
    win = 24000 * 10
    chunks = [wav[i : i + win] for i in range(0, max(1, len(wav) - win + 1), win)]
    with torch.no_grad():
        emb = mulan(wavs=torch.tensor(np.stack(chunks)).to(device)).cpu().numpy()
    r["t_embed"] = time.time() - t
    r["n_windows"], r["emb_dim"] = emb.shape
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    r["intro_outro_cos"] = round(float(emb[:2].mean(0) @ emb[-2:].mean(0)), 3)
    np.save(OUT / f"{path.stem}.npy", emb)

    r["duration"] = round(len(wav) / 24000, 1)
    rows.append(r)
    print(json.dumps(r))

with torch.no_grad():
    txt = mulan(texts=["upbeat funky electronic", "calm gentle piano", "jazzy lounge music"]).cpu().numpy()
txt /= np.linalg.norm(txt, axis=1, keepdims=True)
for path in AUDIO:
    e = np.load(OUT / f"{path.stem}.npy").mean(0)
    e /= np.linalg.norm(e)
    print(path.stem, "text sims:", np.round(txt @ e, 3).tolist())

total = sum(r["t_structure"] + r["t_essentia"] + r["t_embed"] for r in rows)
dur = sum(r["duration"] for r in rows)
print(f"\nTOTAL {total:.1f}s for {dur / 60:.1f} min of audio -> {total / len(rows):.1f}s/track, "
      f"{total / dur * 210:.1f}s per 3.5-min song")
