"""Learn transition weights from 👍/👎 ratings.

Each rated transition has its seven unweighted cost terms x (sound, tempo, key, ...). We fit a
logistic model P(good) = σ(b − w·x) with w ≥ 0, regularised toward the current weights so a
handful of ratings nudges the scoring rather than rewriting it. The tuned weights are only
worth applying if, on ratings held out from fitting, they rank good transitions above bad ones
better than the current weights do (AUC, averaged over several random splits).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from griot_core.cost import TRANSITION_TERMS

MIN_RATINGS = 100  # below this, tuning reports progress but never applies
MIN_EACH = 20  # need enough of both 👍 and 👎 to learn anything
MIN_GAIN = 0.01  # held-out AUC must improve by at least this much


@dataclass
class TuningReport:
    n: int
    up: int
    down: int
    auc_current: float
    auc_tuned: float
    current: dict[str, float]
    tuned: dict[str, float]
    apply: bool
    reason: str


def features(terms: dict[str, float], weights: dict[str, float]) -> np.ndarray | None:
    """Recover unweighted terms from a stored transition (terms are saved already weighted)."""
    out = []
    for k in TRANSITION_TERMS:
        w = weights.get(k, 0.0)
        if w <= 0:
            return None
        out.append(terms.get(k, 0.0) / w)
    return np.array(out)


def auc(scores: np.ndarray, y: np.ndarray) -> float:
    """Probability a random good transition scores above a random bad one (ties count half)."""
    pos, neg = scores[y == 1], scores[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    diff = pos[:, None] - neg[None, :]
    return float((diff > 0).mean() + 0.5 * (diff == 0).mean())


def fit(
    X: np.ndarray, y: np.ndarray, prior: np.ndarray, l2: float = 2.0, iters: int = 3000, lr: float = 0.05
):
    """Non-negative logistic regression, pulled toward `prior`. Returns (w, b)."""
    w, b = (
        prior.astype(float).copy(),
        float(np.log((y.mean() + 1e-3) / (1 - y.mean() + 1e-3)) + X.mean(0) @ prior),
    )
    n = len(y)
    for _ in range(iters):
        z = b - X @ w
        p = 1 / (1 + np.exp(-z))
        g = p - y  # d loss / d z
        grad_w = -(X.T @ g) / n + l2 * (w - prior) / n
        grad_b = g.mean()
        w = np.maximum(0.0, w - lr * grad_w)
        b -= lr * grad_b
    return w, b


def tune(
    X: np.ndarray, y: np.ndarray, current: dict[str, float], splits: int = 8, seed: int = 0
) -> TuningReport:
    prior = np.array([current[k] for k in TRANSITION_TERMS])
    n, up = len(y), int(y.sum())
    down = n - up

    def report(auc_c, auc_t, tuned, apply, reason):
        return TuningReport(n, up, down, auc_c, auc_t, dict(current), tuned, apply, reason)

    if n < MIN_RATINGS or up < MIN_EACH or down < MIN_EACH:
        need = f"{max(0, MIN_RATINGS - n)} more ratings" if n < MIN_RATINGS else "more of both 👍 and 👎"
        return report(
            float("nan"), float("nan"), dict(current), False, f"not enough ratings yet: need {need}"
        )

    rng = np.random.default_rng(seed)
    a_cur, a_new = [], []
    for _ in range(splits):  # stratified 75/25 splits
        test = np.zeros(n, bool)
        for cls in (0, 1):
            idx = np.flatnonzero(y == cls)
            test[rng.choice(idx, max(1, len(idx) // 4), replace=False)] = True
        w, _ = fit(X[~test], y[~test], prior)
        a_cur.append(auc(-X[test] @ prior, y[test]))
        a_new.append(auc(-X[test] @ w, y[test]))
    auc_c, auc_t = float(np.nanmean(a_cur)), float(np.nanmean(a_new))

    w, _ = fit(X, y, prior)
    w = w * (prior.sum() / max(w.sum(), 1e-9))  # keep the overall balance with progress/arc terms
    tuned = dict(current) | {k: round(float(v), 4) for k, v in zip(TRANSITION_TERMS, w, strict=True)}
    better = auc_t >= auc_c + MIN_GAIN
    reason = (
        f"tuned scoring agrees with your ratings better on held-out data (AUC {auc_c:.3f} → {auc_t:.3f})"
        if better
        else f"tuned scoring isn't clearly better on held-out data (AUC {auc_c:.3f} → {auc_t:.3f}); "
        "keeping current"
    )
    return report(auc_c, auc_t, tuned, better, reason)
