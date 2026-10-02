"""Offline evaluation: Griot vs baselines on the same waypoint pairs.

Every method *plans* with whatever information it is allowed, but every bridge is *judged*
on full-song information. Primary metrics are raw, interpretable quantities (seam
similarity, tempo jump %, Camelot steps, energy jump) rather than Griot's own objective,
so the comparison does not simply reward the optimizer for agreeing with itself.

The `preview` ablation is the thesis test: plan with what a 30 s preview provides
(one mid-song embedding, global loudness/mood, no intro/outro) and see how much worse
the real seams get.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from griot_core.catalog import Catalog
from griot_core.cost import merged_weights, transition_cost
from griot_core.pathfinder import Pathfinder, slerp
from griot_core.schema import TrackFeatures


@dataclass
class BridgeMetrics:
    seam_sim: float  # mean cos(outro_A, intro_B), higher is better
    worst_seam_sim: float
    tempo_jump_pct: float  # mean |Δ log bpm| (half/double-folded), as %
    tempo_violations: float  # share of transitions jumping > 8% tempo
    key_steps: float  # mean Camelot steps
    key_clashes: float  # share of transitions with >= 3 Camelot steps
    energy_jump: float  # mean |energy_end_A - energy_start_B| (0..1 scale)
    progress_rho: float  # Spearman(step, similarity to target): steady progress -> 1
    artist_repeat: float  # share of tracks whose artist already appeared
    judge_cost: float  # mean full-information transition cost (Griot's objective)


def _fold(r: np.ndarray) -> np.ndarray:
    x = np.abs(np.log(r))
    return np.minimum.reduce([x, np.abs(x - np.log(2)), np.abs(x + np.log(2))])


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def measure(cat: Catalog, path: list[int], weights: dict[str, float] | None = None) -> BridgeMetrics:
    w = merged_weights(weights)
    a, b = np.array(path[:-1]), np.array(path[1:])
    seam = np.einsum("ij,ij->i", cat.outro[a], cat.intro[b])
    tempo = _fold(cat.bpm[a] / cat.bpm[b])
    d = np.abs(cat.cam_num[a] - cat.cam_num[b])
    steps = np.minimum(d, 12 - d) + (cat.cam_letter[a] != cat.cam_letter[b])
    energy = np.abs(cat.e_end[a] - cat.e_start[b])
    to_target = cat.full[path] @ cat.full[path[-1]]
    seen, repeats = set(), 0
    for i in path:
        repeats += cat.artist_key[i] in seen
        seen.add(cat.artist_key[i])
    judge = [transition_cost(cat, [x], [y], w)[0, 0] for x, y in zip(a, b)]
    return BridgeMetrics(
        seam_sim=float(seam.mean()),
        worst_seam_sim=float(seam.min()),
        tempo_jump_pct=float(100 * (np.exp(tempo) - 1).mean()),
        tempo_violations=float((tempo > np.log(1.08)).mean()),
        key_steps=float(steps.mean()),
        key_clashes=float((steps >= 3).mean()),
        energy_jump=float(energy.mean()),
        progress_rho=_spearman(np.arange(len(path)), to_target),
        artist_repeat=repeats / len(path),
        judge_cost=float(np.mean(judge)),
    )


# ------------------------------------------------------------------------- baselines


def random_bridge(cat: Catalog, s: int, e: int, n: int, rng: np.random.Generator) -> list[int]:
    pool = np.setdiff1d(np.arange(len(cat)), [s, e])
    return [s, *rng.choice(pool, n, replace=False).tolist(), e]


def straight_line(cat: Catalog, s: int, e: int, n: int) -> list[int]:
    """Nearest track to each interpolation point on emb_full — similarity only, no seams."""
    path = [s]
    for i in range(1, n + 1):
        t = slerp(cat.full[s], cat.full[e], i / (n + 1))
        for x in np.argsort(-(cat.full @ t)):
            if x not in path and x != e:
                path.append(int(x))
                break
    return path + [e]


def artist_graph(cat: Catalog, s: int, e: int, n: int, k: int = 8) -> list[int]:
    """Boil-the-Frog style: shortest path on an artist-similarity graph, then for each
    artist the track that minimises the energy jump from the previous pick."""
    by_artist: dict[str, list[int]] = defaultdict(list)
    for i, a in enumerate(cat.artist_key):
        by_artist[a].append(i)
    names = list(by_artist)
    cent = np.array([cat.full[by_artist[a]].mean(0) for a in names])
    cent /= np.linalg.norm(cent, axis=1, keepdims=True)
    sims = cent @ cent.T
    nbrs = np.argsort(-sims, axis=1)[:, 1 : k + 1]
    src, dst = names.index(cat.artist_key[s]), names.index(cat.artist_key[e])
    dist, prev, pq = {src: 0.0}, {}, [(0.0, src)]
    while pq:
        dcur, u = heapq.heappop(pq)
        if u == dst:
            break
        if dcur > dist.get(u, np.inf):
            continue
        for v in nbrs[u]:
            nd = dcur + (1 - sims[u, v])
            if nd < dist.get(v, np.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    chain = [dst]
    while chain[-1] != src and chain[-1] in prev:
        chain.append(prev[chain[-1]])
    chain = chain[::-1][1:-1]
    # resample the artist chain to exactly n stops
    if chain:
        chain = [chain[int(i * len(chain) / n)] for i in range(n)]
    path, used = [s], {s, e}
    for ai in chain:
        cands = [t for t in by_artist[names[ai]] if t not in used] or [
            t for t in np.argsort(-(cat.full @ cent[ai])) if t not in used
        ][:1]
        best = min(cands, key=lambda t: abs(cat.e_start[t] - cat.e_end[path[-1]]))
        path.append(int(best))
        used.add(int(best))
    while len(path) < n + 1:  # disconnected graph: pad along the straight line
        path = straight_line(cat, s, e, n)[: n + 1]
    return path + [e]


def preview_view(tracks: list[TrackFeatures]) -> list[TrackFeatures]:
    """What a planner would know from 30 s previews: one hook-ish embedding used for
    full/intro/outro, flat energy and mood at the global values."""
    out = []
    for t in tracks:
        c = t.model_copy(deep=True)
        hook = c.embeddings.chorus or c.embeddings.full
        c.embeddings.full = c.embeddings.intro = c.embeddings.outro = hook
        c.trajectory.energy = [c.global_.lufs] * max(1, len(c.trajectory.energy))
        c.trajectory.valence, c.trajectory.arousal = [], []
        out.append(c)
    return out


def run(
    tracks: list[TrackFeatures], pairs: int = 50, n: int = 8, seed: int = 0
) -> dict[str, list[BridgeMetrics]]:
    cat = Catalog(tracks)
    prev_cat = Catalog(preview_view(tracks))
    rng = np.random.default_rng(seed)
    results: dict[str, list[BridgeMetrics]] = defaultdict(list)
    methods = {
        "griot": lambda s, e: Pathfinder(cat).bridge([s, e], [n]).path,
        "griot (preview-only)": lambda s, e: Pathfinder(prev_cat).bridge([s, e], [n]).path,
        "straight line (emb)": lambda s, e: straight_line(cat, s, e, n),
        "artist graph (BtF)": lambda s, e: artist_graph(cat, s, e, n),
        "random": lambda s, e: random_bridge(cat, s, e, n, rng),
    }
    for _ in range(pairs):
        s, e = (int(x) for x in rng.choice(len(cat), 2, replace=False))
        for name, fn in methods.items():
            try:
                results[name].append(measure(cat, fn(s, e)))
            except ValueError:
                pass
    return dict(results)


def table(results: dict[str, list[BridgeMetrics]]) -> str:
    cols = [
        ("seam_sim", "seam sim ↑"),
        ("worst_seam_sim", "worst seam ↑"),
        ("tempo_jump_pct", "tempo jump % ↓"),
        ("key_clashes", "key clashes ↓"),
        ("energy_jump", "energy jump ↓"),
        ("progress_rho", "progress ρ ↑"),
        ("artist_repeat", "artist repeat ↓"),
        ("judge_cost", "judge cost ↓"),
    ]
    lines = ["| method | n | " + " | ".join(c[1] for c in cols) + " |", "|---|---" + "|---" * len(cols) + "|"]
    for name, ms in results.items():
        vals = [f"{np.mean([getattr(m, k) for m in ms]):.3f}" for k, _ in cols]
        lines.append(f"| {name} | {len(ms)} | " + " | ".join(vals) + " |")
    return "\n".join(lines)
