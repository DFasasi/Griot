# Day-1 model spike (Apple Silicon, MPS, 24 GB)

Script: `spike/run_spike.py` on 5 full-length CC-BY Kevin MacLeod tracks (1–3.5 min).

| Stage | Tool | Time / track | Notes |
|---|---|---|---|
| Structure (beats, downbeats, segments) | `all-in-one-mlx` | 11–80 s (bottleneck; includes Demucs) | `allin1` upstream is broken with current NATTEN; use `all-in-one-mlx` on Mac, `all-in-one-infer` (pure PyTorch) on Linux/Modal |
| Key / BPM / loudness / danceability | Essentia 2.1b6 | ~2 s | BPM octave disagreement vs allin1 on 1/5 tracks (164 vs 82) → merge rule + half/double-tolerant cost |
| Embeddings (10 s windows, 512-d) | MuQ-MuLan-large | 6–13 s | Requires `transformers<4.50`. Text→audio similarity is meaningful (Vibe Ace: "upbeat funky" 0.32 vs "calm piano" −0.14) |

**Throughput:** ~68 s per 3.5-min song → ~50 songs/hour → a 1,000-song library overnight-ish (~19 h).
Corpus backfill therefore runs on Modal GPUs, and the local analyzer is resumable.

**Caveats:** segment labels on instrumental library music are weak (mostly "solo"/"intro") —
Harmonix-trained model expects pop song form; verify on vocal pop tracks. Essentia
`Danceability` ranges 0–3 and must be normalized.
