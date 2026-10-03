"""Bridge pathfinder: layered k-best Viterbi between consecutive waypoints.

For a gap S -> T with n bridge tracks, step i gets a target point
t_i = slerp(full_S, full_T, i / (n + 1)) in embedding space. Candidates for step i are
the tracks nearest t_i; dynamic programming then picks one candidate per layer to minimise

    sum(transition_cost) + sum(position_cost)

where position_cost keeps the bridge moving steadily from S to T (progress), optionally
follows a user-drawn energy/valence arc, and optionally leans toward a text prompt.
Each node keeps its `beam` best partial paths so no-repeat / per-artist constraints
can be enforced without losing the near-optimal alternatives.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from griot_core.catalog import Catalog
from griot_core.cost import explain, merged_weights, transition_cost
from griot_core.schema import ArcPoint


@dataclass
class Leg:
    path: list[int]  # catalog indices, including waypoints
    cost: float
    transitions: list[dict] = field(default_factory=list)


def slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    omega = np.arccos(np.clip(a @ b, -1.0, 1.0))
    if omega < 1e-6:
        return a
    return (np.sin((1 - t) * omega) * a + np.sin(t * omega) * b) / np.sin(omega)


def interp_arc(arc: list[ArcPoint], t: float, attr: str) -> float | None:
    pts = sorted((p.t, getattr(p, attr)) for p in arc if getattr(p, attr) is not None)
    if not pts:
        return None
    xs, ys = zip(*pts, strict=True)
    return float(np.interp(t, xs, ys))


def allocate_gaps(cat: Catalog, waypoints: list[int], total: int) -> list[int]:
    """Split `total` bridge tracks across gaps in proportion to embedding distance (min 1)."""
    gaps = len(waypoints) - 1
    if total < gaps:
        raise ValueError("length must be at least the number of gaps")
    d = np.array(
        [np.arccos(np.clip(cat.full[a] @ cat.full[b], -1, 1)) for a, b in zip(waypoints, waypoints[1:])]
    )
    d = d + 1e-3
    raw = 1 + (total - gaps) * d / d.sum()
    alloc = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - alloc))[: total - alloc.sum()]:
        alloc[i] += 1
    return alloc.tolist()


class Pathfinder:
    def __init__(
        self,
        cat: Catalog,
        weights: dict[str, float] | None = None,
        candidates: int = 200,
        beam: int = 5,
        max_per_artist: int = 1,
    ) -> None:
        self.cat = cat
        self.w = merged_weights(weights)
        self.k = candidates
        self.beam = beam
        self.max_per_artist = max_per_artist

    # ------------------------------------------------------------------ public

    def bridge(
        self,
        waypoints: list[int],
        gap_lengths: list[int],
        allowed: np.ndarray | None = None,
        arc: list[ArcPoint] | None = None,
        steer: np.ndarray | None = None,
    ) -> Leg:
        cat = self.cat
        allowed = np.ones(len(cat), bool) if allowed is None else allowed.copy()
        allowed[waypoints] = False
        used: set[int] = set(waypoints)
        artists = Counter(cat.artist_key[i] for i in waypoints)
        total = sum(gap_lengths) + len(waypoints)

        path, cost, offset = [waypoints[0]], 0.0, 1
        for (s, e), n in zip(zip(waypoints, waypoints[1:]), gap_lengths, strict=True):
            positions = [(offset + i) / (total - 1) for i in range(n)]
            mids, c = self._gap(s, e, n, positions, allowed, used, artists, arc, steer)
            for m in mids:
                used.add(m)
                artists[cat.artist_key[m]] += 1
            path += mids + [e]
            cost += c
            offset += n + 1

        transitions = []
        for a, b in zip(path, path[1:]):
            terms = explain(cat, a, b, self.w)
            transitions.append({"from": a, "to": b, "cost": round(sum(terms.values()), 4), "terms": terms})
        return Leg(path=path, cost=cost, transitions=transitions)

    # ------------------------------------------------------------------ internals

    def _position_cost(self, cand: np.ndarray, target: np.ndarray, pos: float, arc, steer) -> np.ndarray:
        cat, w = self.cat, self.w
        aim = target
        if steer is not None:
            aim = aim + w["steer"] * steer / np.linalg.norm(steer)
            aim = aim / np.linalg.norm(aim)
        c = w["progress"] * cat.full_dist(cat.full[cand] @ aim)
        if arc:
            te = interp_arc(arc, pos, "energy")
            if te is not None:
                c = c + w["arc"] * np.abs(cat.e_mean[cand] - te) / 0.5
            tv = interp_arc(arc, pos, "valence")
            if tv is not None:
                c = c + w["arc"] * np.nan_to_num(np.abs(cat.valence[cand] - tv) / 0.5, nan=0.5)
        return c

    def _candidates(self, aim: np.ndarray, allowed: np.ndarray) -> np.ndarray:
        idx = np.flatnonzero(allowed)
        if len(idx) <= self.k:
            return idx
        sims = self.cat.full[idx] @ aim
        return idx[np.argpartition(-sims, self.k)[: self.k]]

    def _seam(self, f, t) -> np.ndarray:
        """Transition cost as the search sees it: convex, so rough seams are avoided first."""
        c = transition_cost(self.cat, f, t, self.w)
        return c + self.w["rough"] * c * c

    def _gap(self, s, e, n, positions, allowed, used, artists, arc, steer):
        cat, w = self.cat, self.w
        if n == 0:
            return [], float(self._seam([s], [e])[0, 0])

        layers, pcosts = [], []
        for i in range(n):
            target = slerp(cat.full[s], cat.full[e], (i + 1) / (n + 1)).astype(np.float32)
            aim = target if steer is None else target + w["steer"] * steer / np.linalg.norm(steer)
            cand = self._candidates(aim / np.linalg.norm(aim), allowed)
            if len(cand) == 0:
                raise ValueError("no candidate tracks satisfy the filters")
            layers.append(cand)
            pcosts.append(self._position_cost(cand, target, positions[i], arc, steer))

        used_recs = {cat.rec_key[u] for u in used}

        def ok(path: tuple[int, ...], x: int) -> bool:
            if x in used or x in path:
                return False
            rk = cat.rec_key[x]
            if rk in used_recs or any(cat.rec_key[p] == rk for p in path):
                return False
            ak = cat.artist_key[x]
            return artists[ak] + sum(cat.artist_key[p] == ak for p in path) < self.max_per_artist

        # paths[node_pos] -> list of (cost, path tuple); layer 0 seeded from S
        c0 = self._seam([s], layers[0])[0] + pcosts[0]
        paths = [[(float(c0[j]), (int(x),))] if ok((), int(x)) else [] for j, x in enumerate(layers[0])]

        for i in range(1, n):
            prev, cur = layers[i - 1], layers[i]
            C = self._seam(prev, cur)  # (len prev, len cur)
            owner = np.array([p for p, plist in enumerate(paths) for _ in plist])
            flat = [pp for plist in paths for pp in plist]
            if not flat:
                raise ValueError("constraints left no valid path; relax filters or max_per_artist")
            base = np.array([c for c, _ in flat])
            new_paths = []
            for j, x in enumerate(cur):
                x = int(x)
                tot = base + C[owner, j] + pcosts[i][j]
                keep = []
                for q in np.argsort(tot):
                    if ok(flat[q][1], x):
                        keep.append((float(tot[q]), flat[q][1] + (x,)))
                        if len(keep) == self.beam:
                            break
                new_paths.append(keep)
            paths = new_paths

        last = layers[-1]
        Ce = self._seam(last, [e])[:, 0]
        best = min(
            ((c + float(Ce[j]), p) for j, plist in enumerate(paths) for c, p in plist),
            default=None,
        )
        if best is None:
            raise ValueError("constraints left no valid path; relax filters or max_per_artist")
        return list(best[1]), best[0]
