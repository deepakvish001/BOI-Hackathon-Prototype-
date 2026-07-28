"""Finite-difference checks for the hand-written backward passes.

Both neural layers compute their gradients analytically, which is exactly the
kind of code that is quietly wrong for months. Every parameter tensor is
checked against a central difference of the real loss.
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.sparse as sp

from bodhi.config import GraphSAGEConfig, TemporalConfig
from bodhi.models.graphsage import GraphSAGE, _sigmoid
from bodhi.models.temporal import N_EVENT_FEATURES, TemporalGraphNetwork

TOL = 2e-5


def _bce(logits: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(_sigmoid(logits), 1e-12, 1 - 1e-12)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def _numeric(fn, param: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Central-difference gradient of ``fn`` with respect to ``param`` in place."""
    grad = np.zeros_like(param)
    it = np.nditer(param, flags=["multi_index"])
    while not it.finished:
        i = it.multi_index
        old = param[i]
        param[i] = old + eps
        hi = fn()
        param[i] = old - eps
        lo = fn()
        param[i] = old
        grad[i] = (hi - lo) / (2 * eps)
        it.iternext()
    return grad


def test_graphsage_gradients_match_finite_differences():
    rng = np.random.default_rng(0)
    n, d = 12, 5
    cfg = GraphSAGEConfig(hidden_dims=(6, 4), dropout=0.0)
    model = GraphSAGE(cfg)
    model._init_params(d, rng)

    A = (rng.random((n, n)) < 0.35).astype(float)
    np.fill_diagonal(A, 0)
    A = A / np.maximum(A.sum(axis=1, keepdims=True), 1.0)
    P = sp.csr_matrix(A)
    model.P = [P, P]
    model.PT = [P.T.tocsr(), P.T.tocsr()]

    X = rng.standard_normal((n, d))
    y = (rng.random(n) < 0.4).astype(float)

    def loss() -> float:
        return _bce(model._forward(X, training=False)["logits"], y)

    cache = model._forward(X, training=False)
    p = _sigmoid(cache["logits"])
    analytic = model._backward(cache, (p - y) / n)

    for name, param in model.params.items():
        num = _numeric(loss, param)
        err = np.abs(num - analytic[name]).max()
        scale = max(np.abs(num).max(), 1.0)
        assert err / scale < TOL, f"GraphSAGE gradient mismatch for {name}: {err:.3e}"


@pytest.mark.parametrize("time_dim", [4])
def test_tgn_gradients_match_finite_differences(time_dim):
    rng = np.random.default_rng(1)
    B, T = 5, 6
    cfg = TemporalConfig(hidden_dim=6, time_dim=time_dim, max_events=T)
    model = TemporalGraphNetwork(cfg)
    model._init(rng)

    X = rng.standard_normal((B, T, N_EVENT_FEATURES)) * 0.5
    M = (rng.random((B, T)) < 0.8).astype(float)
    M[:, -1] = 1.0  # every account has at least its most recent event
    y = (rng.random(B) < 0.5).astype(float)

    def loss() -> float:
        return _bce(model._forward(X, M)["logits"], y)

    cache = model._forward(X, M)
    p = _sigmoid(cache["logits"])
    analytic = model._backward(cache, (p - y) / B)

    for name, param in model.params.items():
        num = _numeric(loss, param)
        err = np.abs(num - analytic[name]).max()
        scale = max(np.abs(num).max(), 1.0)
        assert err / scale < TOL, f"TGN gradient mismatch for {name}: {err:.3e}"


def test_tgn_mask_blocks_padded_events():
    """A fully masked step must leave the memory untouched."""
    rng = np.random.default_rng(2)
    cfg = TemporalConfig(hidden_dim=5, time_dim=3, max_events=4)
    model = TemporalGraphNetwork(cfg)
    model._init(rng)

    X = rng.standard_normal((2, 4, N_EVENT_FEATURES))
    M = np.ones((2, 4))
    M[:, 0] = 0.0
    h_masked = model._forward(X, M)["h_final"]

    # Same events, padding removed: the surviving suffix must give the same state.
    h_short = model._forward(X[:, 1:, :], np.ones((2, 3)))["h_final"]
    assert np.allclose(h_masked, h_short, atol=1e-12)
