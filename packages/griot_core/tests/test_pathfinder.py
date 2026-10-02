import numpy as np
import pytest

from griot_core import Catalog, Pathfinder, allocate_gaps, transition_cost
from griot_core.cost import TRANSITION_TERMS, merged_weights
from griot_core.schema import ArcPoint, BridgeRequest, TrackFeatures
from griot_core.synthetic import make_tracks


@pytest.fixture(scope="module")
def cat():
    return Catalog(make_tracks(n=1500, seed=1))


def far_pair(cat):
    sims = cat.full @ cat.full.T
    a, b = np.unravel_index(np.argmin(sims), sims.shape)
    return int(a), int(b)


def test_schema_roundtrip_uses_global_alias(cat):
    t = cat.tracks[0]
    data = t.model_dump(by_alias=True)
    assert "global" in data and "global_" not in data
    assert TrackFeatures.model_validate(data) == t


def test_terms_are_normalized(cat):
    idx = np.arange(50)
    from griot_core.cost import transition_terms

    for name, m in transition_terms(cat, idx, idx).items():
        assert m.shape == (50, 50), name
        assert np.all((m >= 0) & (m <= 1)), name
    assert set(TRANSITION_TERMS) == set(transition_terms(cat, idx[:1], idx[:1]))


def test_bridge_shape_and_constraints(cat):
    a, b = far_pair(cat)
    pf = Pathfinder(cat, max_per_artist=1)
    leg = pf.bridge([a, b], [10])
    assert leg.path[0] == a and leg.path[-1] == b and len(leg.path) == 12
    assert len(set(leg.path)) == len(leg.path)
    mids = leg.path[1:-1]
    artists = [cat.artist_key[i] for i in leg.path]
    assert len(set(artists)) == len(artists)
    assert len(leg.transitions) == 11
    assert all(set(t["terms"]) == set(TRANSITION_TERMS) for t in leg.transitions)
    assert not {a, b} & set(mids)


def test_bridge_progresses_toward_target(cat):
    a, b = far_pair(cat)
    leg = Pathfinder(cat).bridge([a, b], [10])
    sim_to_b = [float(cat.full[i] @ cat.full[b]) for i in leg.path]
    rho = np.corrcoef(np.arange(len(sim_to_b)), np.argsort(np.argsort(sim_to_b)))[0, 1]
    assert rho > 0.8


def test_bridge_beats_random_and_greedy_baselines(cat):
    a, b = far_pair(cat)
    w = merged_weights(None)
    leg = Pathfinder(cat).bridge([a, b], [10])

    def mean_cost(path):
        return float(np.mean([transition_cost(cat, [x], [y], w)[0, 0] for x, y in zip(path, path[1:])]))

    rng = np.random.default_rng(0)
    rand = [a, *rng.choice(len(cat), 10, replace=False).tolist(), b]
    # embedding straight-line baseline: nearest track to each interpolation point, no seam awareness
    from griot_core.pathfinder import slerp

    line = [a]
    for i in range(1, 11):
        t = slerp(cat.full[a], cat.full[b], i / 11)
        order = np.argsort(-(cat.full @ t))
        line.append(int(next(x for x in order if x not in line and x != b)))
    line.append(b)
    assert mean_cost(leg.path) < mean_cost(rand)
    assert mean_cost(leg.path) < mean_cost(line)


def test_viterbi_is_optimal_on_unconstrained_tiny_problem():
    cat = Catalog(make_tracks(n=40, seed=3, n_artists=40))
    pf = Pathfinder(cat, candidates=40, beam=40, max_per_artist=99)
    a, b = 0, 1
    leg = pf.bridge([a, b], [2])
    w = pf.w
    from griot_core.pathfinder import slerp

    best = np.inf
    for x in range(2, 40):
        for y in range(2, 40):
            if x == y:
                continue
            c = transition_cost(cat, [a], [x], w)[0, 0] + transition_cost(cat, [x], [y], w)[0, 0]
            c += transition_cost(cat, [y], [b], w)[0, 0]
            for i, z in enumerate((x, y)):
                t = slerp(cat.full[a], cat.full[b], (i + 1) / 3)
                c += w["progress"] * cat.full_dist(cat.full[z] @ t)
            best = min(best, c)
    assert leg.cost == pytest.approx(best, rel=1e-5)


def test_multi_waypoint_allocation_and_arc(cat):
    a, b = far_pair(cat)
    mid = int(np.argmax(cat.full @ (cat.full[a] + cat.full[b])))
    wps = [a, mid, b]
    gaps = allocate_gaps(cat, wps, 9)
    assert sum(gaps) == 9 and all(g >= 1 for g in gaps)
    arc = [ArcPoint(t=0, energy=0.2), ArcPoint(t=0.5, energy=0.9), ArcPoint(t=1, energy=0.2)]
    leg = Pathfinder(cat).bridge(wps, gaps, arc=arc)
    assert len(leg.path) == 12 and leg.path[1 + gaps[0]] == mid


def test_bridge_request_validates_gap_lengths():
    with pytest.raises(ValueError):
        BridgeRequest(waypoints=["a", "b", "c"], gap_lengths=[3])
