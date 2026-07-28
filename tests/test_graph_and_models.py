"""Graph construction, ring extraction, flow tracing and the model layers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bodhi.graph import build_graph, detect_rings, trace_flows
from bodhi.graph.builder import EDGE_TYPES, MAX_SHARED_FANOUT, topology_features
from bodhi.models.fusion import RiskFusion
from bodhi.models.graphsage import build_propagation
from bodhi.models.temporal import build_sequences, N_EVENT_FEATURES


# ------------------------------------------------------------------ graph


def test_graph_is_symmetric_and_well_formed(graph):
    assert graph.n_nodes > 0
    assert graph.indptr[0] == 0
    assert graph.indptr[-1] == graph.n_edges
    assert np.all(np.diff(graph.indptr) >= 0)
    # No self-loops.
    row = np.repeat(np.arange(graph.n_nodes), np.diff(graph.indptr))
    assert not np.any(row == graph.indices)


def test_adjacency_is_undirected(graph):
    """Every edge must appear in both directions for message passing."""
    row = np.repeat(np.arange(graph.n_nodes), np.diff(graph.indptr))
    forward = set(zip(row.tolist(), graph.indices.tolist()))
    backward = {(b, a) for a, b in forward}
    assert forward == backward


def test_shared_identifier_cliques_are_capped(sim):
    """A CGNAT pool must not become a 4000-way clique."""
    g = build_graph(sim.transactions, sim.accounts)
    row = np.repeat(np.arange(g.n_nodes), np.diff(g.indptr))
    for etype in (EDGE_TYPES["DEVICE"], EDGE_TYPES["IP"]):
        mask = g.etypes == etype
        if not mask.any():
            continue
        degree = np.bincount(row[mask], minlength=g.n_nodes)
        assert degree.max() <= MAX_SHARED_FANOUT * 4


def test_money_edges_never_point_at_the_cash_sink(graph, sim):
    from bodhi.data.generator import CASH_POOL
    assert CASH_POOL not in set(graph.index)


def test_topology_features_are_finite(graph):
    topo = topology_features(graph)
    assert np.isfinite(topo.to_numpy()).all()
    # PageRank is a distribution.
    assert topo["pagerank"].sum() == pytest.approx(1.0, abs=1e-6)


def test_propagation_matrix_rows_sum_to_one(graph):
    P = build_propagation(graph, fanout=8)
    sums = np.asarray(P.sum(axis=1)).ravel()
    nonzero = sums > 0
    np.testing.assert_allclose(sums[nonzero], 1.0, atol=1e-9)
    # Fan-out cap must actually bind.
    assert np.diff(P.indptr).max() <= 8


# ------------------------------------------------------------------ rings


def test_rings_require_a_flagged_majority(graph):
    """One flagged account inside a big shared-device cluster is not a ring."""
    risk = pd.Series(0.0, index=graph.index)
    # A single hot account sitting in whatever cluster it belongs to.
    risk.iloc[0] = 99.0
    rings = detect_rings(graph, risk, threshold=55.0)
    assert rings == []


def test_rings_are_found_for_real_clusters(graph, sim):
    truth = sim.accounts.set_index("account_id")["is_mule"]
    risk = pd.Series(np.where(truth.reindex(graph.index).fillna(False), 95.0, 3.0),
                     index=graph.index)
    rings = detect_rings(graph, risk, threshold=55.0)
    assert rings, "no rings recovered even with perfect scores"
    for r in rings:
        assert r.size >= 3
        assert r.mean_risk >= 50.0
        assert set(r.members) <= set(graph.index)


def test_ring_members_are_only_flagged_accounts(graph, sim):
    """Innocent accounts linked by a shared handset must not be listed as
    members of a 'fraud ring'."""
    truth = sim.accounts.set_index("account_id")["is_mule"]
    risk = pd.Series(np.where(truth.reindex(graph.index).fillna(False), 95.0, 3.0),
                     index=graph.index)
    rings = detect_rings(graph, risk, threshold=55.0)
    assert rings
    for r in rings:
        # Members are exactly the accounts that cleared the threshold.
        assert all(risk[m] >= 55.0 for m in r.members)
        # Associated accounts are reported, but separately, and never overlap.
        assert not (set(r.members) & set(r.associated))
        assert all(risk[a] < 55.0 for a in r.associated)
        assert r.size == len(r.members)
        assert r.footprint == len(r.members) + len(r.associated)


def test_ring_membership_is_precise_against_ground_truth(graph, sim):
    """With well-separated scores, ring membership should be almost pure."""
    truth = sim.accounts.set_index("account_id")["is_mule"]
    risk = pd.Series(np.where(truth.reindex(graph.index).fillna(False), 95.0, 3.0),
                     index=graph.index)
    rings = detect_rings(graph, risk, threshold=55.0)
    members = [m for r in rings for m in r.members]
    assert members
    purity = sum(bool(truth.get(m, False)) for m in members) / len(members)
    assert purity > 0.9, f"ring membership purity only {purity:.2f}"


def test_flow_tracing_respects_time(graph, sim):
    """Every hop must be strictly later than the one before it."""
    busy = sim.transactions["src_account"].value_counts().head(20).index
    traced = 0
    for account in busy:
        paths = trace_flows(graph, str(account), max_hops=4, window_hours=72)
        for p in paths:
            times = [h["ts"] for h in p["hops"]]
            assert times == sorted(times), "non-monotone path emitted"
            for a, b in zip(times, times[1:]):
                assert b - a <= 72 * 3600 + 1
            traced += 1
    assert traced > 0, "no paths traced at all"


def test_flow_tracing_does_not_revisit_nodes(graph, sim):
    busy = sim.transactions["src_account"].value_counts().head(10).index
    for account in busy:
        for p in trace_flows(graph, str(account), max_hops=5):
            assert len(p["path"]) == len(set(p["path"]))


# ------------------------------------------------------------------ TGN


def test_sequences_are_right_aligned_and_masked(sim):
    X, M = build_sequences(sim.transactions, sim.accounts, max_events=16)
    assert X.shape == (len(sim.accounts), 16, N_EVENT_FEATURES)
    assert M.shape == (len(sim.accounts), 16)
    assert set(np.unique(M)) <= {0.0, 1.0}
    # Padding is at the front: a zero can never precede a one from the right.
    for row in M[:200]:
        ones = np.flatnonzero(row)
        if ones.size:
            assert ones[-1] == len(row) - 1
            assert np.all(row[ones[0]:] == 1.0)
    # Masked steps carry no data.
    assert np.allclose(X[M == 0], 0.0)


def test_sequences_respect_as_of(sim):
    cut = float(sim.transactions["ts"].quantile(0.5))
    _, M_early = build_sequences(sim.transactions, sim.accounts, 16, as_of=cut)
    _, M_late = build_sequences(sim.transactions, sim.accounts, 16)
    assert M_early.sum() < M_late.sum()


# ------------------------------------------------------------------ fusion


def _fusion_inputs(n=800, seed=0):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.06).astype(int)
    def channel(strength):
        return np.clip(rng.beta(2, 8, n) + y * strength * rng.random(n), 0, 1)
    return {"xgb": channel(0.8), "graph": channel(0.7),
            "temporal": channel(0.5), "intel": channel(0.3)}, y


def test_fusion_weights_are_non_negative():
    scores, y = _fusion_inputs()
    f = RiskFusion().fit(scores, y)
    assert f.weights is not None
    assert (f.weights >= 0).all(), "a negative blend weight breaks monotonicity"


def test_fusion_is_monotone_in_every_layer():
    """Raising any single layer's score must never lower the fused risk."""
    scores, y = _fusion_inputs()
    f = RiskFusion().fit(scores, y)
    base = f.risk_score(scores)
    for layer in ("xgb", "graph", "temporal", "intel"):
        bumped = {k: v.copy() for k, v in scores.items()}
        bumped[layer] = np.clip(bumped[layer] + 0.25, 0, 1)
        assert (f.risk_score(bumped) >= base - 1e-9).all(), \
            f"raising {layer} lowered the fused score"


def test_fusion_beats_or_matches_its_best_input():
    from sklearn.metrics import average_precision_score
    scores, y = _fusion_inputs(n=3_000, seed=5)
    f = RiskFusion().fit(scores, y)
    fused = average_precision_score(y, f.predict_proba(scores))
    best = max(average_precision_score(y, v) for v in scores.values())
    # Fitted on the same data here, so it should not be materially worse.
    assert fused >= best - 0.05


def test_fusion_falls_back_before_fitting():
    scores, _ = _fusion_inputs(n=100)
    f = RiskFusion()
    out = f.risk_score(scores)
    assert out.shape == (100,)
    assert np.isfinite(out).all()
    assert (out >= 0).all() and (out <= 100).all()


def test_fusion_roundtrips_through_disk(tmp_path):
    scores, y = _fusion_inputs()
    f = RiskFusion().fit(scores, y)
    f.save(tmp_path / "fusion")
    g = RiskFusion.load(tmp_path / "fusion")
    np.testing.assert_allclose(f.predict_proba(scores), g.predict_proba(scores),
                               atol=1e-9)
