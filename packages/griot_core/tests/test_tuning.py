import numpy as np

from griot_core.cost import DEFAULT_WEIGHTS, TRANSITION_TERMS
from griot_core.tuning import MIN_RATINGS, auc, features, tune


def simulated_ratings(n, true_w, seed=0):
    """A listener who, unlike the defaults, mostly cares about mood and barely about key."""
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, size=(n, len(TRANSITION_TERMS)))
    p_good = 1 / (1 + np.exp(-(3.0 - 4.0 * X @ true_w / true_w.sum() * 2)))
    y = (rng.uniform(size=n) < p_good).astype(int)
    return X, y


def test_auc_basics():
    y = np.array([1, 1, 0, 0])
    assert auc(np.array([0.9, 0.8, 0.2, 0.1]), y) == 1.0
    assert auc(np.array([0.1, 0.2, 0.8, 0.9]), y) == 0.0


def test_features_recovers_unweighted_terms():
    w = dict(DEFAULT_WEIGHTS)
    terms = {k: 0.5 * w[k] for k in TRANSITION_TERMS}
    assert np.allclose(features(terms, w), 0.5)


def test_too_few_ratings_never_applies():
    X, y = simulated_ratings(MIN_RATINGS - 1, np.ones(len(TRANSITION_TERMS)))
    r = tune(X, y, DEFAULT_WEIGHTS)
    assert not r.apply and "more ratings" in r.reason and r.tuned == DEFAULT_WEIGHTS


def test_learns_a_listeners_priorities_and_beats_defaults_held_out():
    true_w = np.array([0.4, 0.4, 0.05, 0.5, 3.0, 0.2, 0.1])  # sound tempo key energy mood lyrics popularity
    X, y = simulated_ratings(400, true_w, seed=1)
    r = tune(X, y, DEFAULT_WEIGHTS)
    assert r.apply and r.auc_tuned > r.auc_current
    assert r.tuned["mood"] > DEFAULT_WEIGHTS["mood"] and r.tuned["key"] < DEFAULT_WEIGHTS["key"]
    total = sum(DEFAULT_WEIGHTS[k] for k in TRANSITION_TERMS)
    assert abs(sum(r.tuned[k] for k in TRANSITION_TERMS) - total) < 1e-3  # balance preserved
    assert r.tuned["progress"] == DEFAULT_WEIGHTS["progress"]  # only transition terms change


def test_ratings_that_agree_with_defaults_keep_them():
    true_w = np.array([DEFAULT_WEIGHTS[k] for k in TRANSITION_TERMS])
    X, y = simulated_ratings(300, true_w, seed=2)
    assert not tune(X, y, DEFAULT_WEIGHTS).apply
