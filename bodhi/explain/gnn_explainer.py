"""GNNExplainer for the GraphSAGE layer.

SHAP answers "which of this account's own attributes drove the score". It
cannot answer "which *relationships* drove it" - and for mule detection the
relationships are usually the whole case. GNNExplainer (Ying et al., 2019)
fills that gap by learning a soft mask over the edges of the target's k-hop
neighbourhood, optimised so that keeping only the masked edges reproduces the
original prediction while the mask stays sparse.

The optimisation runs on the extracted sub-graph in dense form. Two hops of a
capped-fan-out graph is a few hundred nodes, so a couple of hundred gradient
steps finish in milliseconds - fast enough to run on demand when an
investigator opens a case, which is the only way an explanation is ever
actually used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bodhi.graph.builder import EDGE_TYPES, FinancialGraph
from bodhi.models.graphsage import GraphSAGE

_EDGE_TYPE_NAME = {v: k for k, v in EDGE_TYPES.items()}


@dataclass
class EdgeAttribution:
    source: str
    target: str
    edge_type: str
    importance: float
    weight: float

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "importance": round(self.importance, 4),
            "weight": round(self.weight, 4),
        }


def _khop(graph: FinancialGraph, node: int, hops: int, max_nodes: int) -> np.ndarray:
    """Breadth-first neighbourhood, strongest edges first when it gets large."""
    frontier = {node}
    seen = {node}
    for _ in range(hops):
        nxt: set[int] = set()
        for v in frontier:
            nbr, w, _ = graph.neighbours(v)
            if nbr.size > 40:
                nbr = nbr[np.argsort(-w)[:40]]
            nxt.update(int(x) for x in nbr)
        nxt -= seen
        seen |= nxt
        frontier = nxt
        if len(seen) >= max_nodes:
            break
    out = np.array(sorted(seen), dtype=np.int64)
    if out.size > max_nodes:
        # Always keep the target itself.
        keep = out[out != node][: max_nodes - 1]
        out = np.sort(np.concatenate([[node], keep]))
    return out


def _local_adjacency(graph: FinancialGraph, nodes: np.ndarray,
                     fanout: int) -> tuple[np.ndarray, np.ndarray]:
    """Dense weighted adjacency of the sub-graph, with the model's fan-out cap."""
    pos = {int(v): i for i, v in enumerate(nodes)}
    k = nodes.size
    W = np.zeros((k, k))
    E = np.zeros((k, k), dtype=np.int8)
    for v in nodes:
        i = pos[int(v)]
        nbr, w, et = graph.neighbours(int(v))
        keep = np.array([j for j, t in enumerate(nbr) if int(t) in pos], dtype=int)
        if keep.size == 0:
            continue
        nbr, w, et = nbr[keep], w[keep], et[keep]
        if nbr.size > fanout:
            sel = np.argsort(-w)[:fanout]
            nbr, w, et = nbr[sel], w[sel], et[sel]
        for t, wt, tp in zip(nbr, w, et):
            j = pos[int(t)]
            W[i, j] = float(wt)
            E[i, j] = int(tp)
    return W, E


def explain_node(
    model: GraphSAGE,
    graph: FinancialGraph,
    X: np.ndarray,
    account_id: str,
    hops: int = 2,
    max_nodes: int = 220,
    steps: int = 300,
    lr: float = 0.20,
    sparsity: float = 0.02,
    entropy_w: float = 0.02,
    top_k: int = 12,
) -> dict:
    """Learn an edge mask explaining the GraphSAGE score for one account."""
    node = graph.code(account_id)
    if node < 0:
        return {"account_id": account_id, "edges": [], "fidelity": 0.0,
                "subgraph_nodes": []}

    nodes = _khop(graph, node, hops, max_nodes)
    pos = {int(v): i for i, v in enumerate(nodes)}
    t = pos[node]
    fanout = int(model.config.fanouts[0]) if model.config.fanouts else 15

    W, E = _local_adjacency(graph, nodes, fanout)
    edge_mask_shape = W > 0
    if not edge_mask_shape.any():
        return {"account_id": account_id, "edges": [], "fidelity": 0.0,
                "subgraph_nodes": [str(graph.index[v]) for v in nodes]}

    Xs = model._standardise(X)[nodes]
    p = model.params
    n_layers = len(model.config.hidden_dims)

    def forward(mask_prob: np.ndarray) -> tuple[float, list, list, np.ndarray]:
        Wm = W * mask_prob
        rowsum = Wm.sum(axis=1, keepdims=True)
        P = np.divide(Wm, rowsum, out=np.zeros_like(Wm), where=rowsum > 0)
        H, Zs, Hs = Xs, [], [Xs]
        for k in range(n_layers):
            A = P @ H
            Z = H @ p[f"Ws{k}"] + A @ p[f"Wn{k}"] + p[f"b{k}"]
            H = np.maximum(Z, 0.0)
            Zs.append(Z)
            Hs.append(H)
        logit = float(H[t] @ p["w"] + p["b_out"][0])
        return logit, Zs, Hs, P

    def backward_to_P(Zs: list, Hs: list, P: np.ndarray) -> np.ndarray:
        dH = np.zeros_like(Hs[-1])
        dH[t] = p["w"]
        dP = np.zeros_like(P)
        for k in range(n_layers - 1, -1, -1):
            dZ = dH * (Zs[k] > 0)
            dA = dZ @ p[f"Wn{k}"].T
            dP += dA @ Hs[k].T
            dH = dZ @ p[f"Ws{k}"].T + P.T @ dA
        return dP

    # Initialise the mask logits near zero (sigmoid ~ 0.5) rather than at
    # "keep everything". Every gradient here carries an s(1-s) factor from the
    # sigmoid, so a mask starting at s ~ 0.73 sits in the flat region where
    # both the fidelity signal and the size penalty vanish - the optimiser then
    # never moves and returns the entire neighbourhood as "the explanation".
    rng = np.random.default_rng(0)
    m = np.zeros_like(W)
    m[edge_mask_shape] = rng.normal(0.0, 0.1, int(edge_mask_shape.sum()))
    base_logit, _, _, _ = forward(np.ones_like(W))

    for _ in range(steps):
        s = 1.0 / (1.0 + np.exp(-np.clip(m, -30, 30)))
        s = np.where(edge_mask_shape, s, 0.0)
        logit, Zs, Hs, P = forward(s)
        dP = backward_to_P(Zs, Hs, P)

        # chain through the row normalisation P = Wm / rowsum(Wm)
        Wm = W * s
        rowsum = Wm.sum(axis=1, keepdims=True)
        corr = (dP * P).sum(axis=1, keepdims=True)
        dWm = np.divide(dP - corr, rowsum, out=np.zeros_like(dP), where=rowsum > 0)
        dS = dWm * W
        g_fid = dS * s * (1.0 - s)
        # The fidelity gradient and the regularisers live on wildly different
        # scales - per-edge fidelity gradients run around 1e-3 on a
        # thousand-edge sub-graph. Normalising the fidelity term to unit
        # magnitude is what makes the sparsity weight mean anything; without
        # it the regularisers are ~5e-5 against it, the mask never moves off
        # 1.0, and "the explanation" is just the whole neighbourhood back.
        g_fid = g_fid / (np.abs(g_fid).max() + 1e-12)

        # Size penalty: push every edge down unless fidelity pays for it.
        g_size = -sparsity * s * (1.0 - s)
        # Entropy penalty: drive the mask towards a crisp 0/1 subset.
        logit_s = np.log(np.clip(s, 1e-9, 1)) - np.log(np.clip(1 - s, 1e-9, 1))
        g_ent = entropy_w * logit_s * s * (1.0 - s)

        # Ascend: we want the masked sub-graph to keep reproducing the score.
        grad = np.sign(base_logit) * g_fid + g_size + g_ent
        m = m + lr * grad
        m = np.clip(m, -12, 12)
        m = np.where(edge_mask_shape, m, -12.0)

    s = np.where(edge_mask_shape, 1.0 / (1.0 + np.exp(-np.clip(m, -30, 30))), 0.0)
    # Fidelity is measured with a *hard* mask: the retained edges keep their
    # full weight and the rest are dropped. Multiplying by the soft mask as
    # well would attenuate every surviving edge and understate fidelity - the
    # question is "do these edges alone reproduce the score", not "does a
    # shrunken copy of them".
    kept = (s > 0.5).astype(float)
    kept_logit, _, _, _ = forward(kept)
    removed_logit, _, _, _ = forward(np.where(edge_mask_shape, 1.0 - kept, 0.0))
    # With every edge deleted, GraphSAGE falls back to its self pathway
    # (W_self . h_v) alone. Comparing that against the full score says how much
    # of this account's graph score came from the account itself rather than
    # from its neighbourhood - and for an account whose own behaviour is already
    # damning, that share is legitimately high. Reporting it stops a low edge
    # fidelity from being read as a broken explainer when it is in fact a
    # correct statement about where the evidence lives.
    isolated_logit, _, _, _ = forward(np.zeros_like(W))
    denom = abs(base_logit) + 1e-6
    self_share = float(min(abs(isolated_logit) / denom, 1.0))

    # Two standard, complementary measures. Reporting only the first would be
    # misleading here: GraphSAGE aggregates by *mean*, so a sub-graph of ten
    # edges renormalises every neighbourhood average and shifts the score even
    # when it contains exactly the right edges. Necessity is the honest headline
    # for a mean aggregator - if deleting the selected edges collapses the
    # score, they were the ones carrying it.
    sufficiency = float(1.0 - abs(kept_logit - base_logit) / denom)
    necessity = float(abs(removed_logit - base_logit) / denom)
    retained = float(kept.sum() / max(edge_mask_shape.sum(), 1))

    idx = np.argsort(-s, axis=None)[:top_k]
    edges: list[EdgeAttribution] = []
    for flat in idx:
        i, j = np.unravel_index(flat, s.shape)
        if not edge_mask_shape[i, j]:
            continue
        edges.append(EdgeAttribution(
            source=str(graph.index[nodes[i]]),
            target=str(graph.index[nodes[j]]),
            edge_type=_EDGE_TYPE_NAME.get(int(E[i, j]), "MONEY"),
            importance=float(s[i, j]),
            weight=float(W[i, j]),
        ))

    return {
        "account_id": account_id,
        "base_logit": round(base_logit, 4),
        "masked_logit": round(kept_logit, 4),
        "fidelity": round(max(min(necessity, 1.0), 0.0), 4),
        "necessity": round(max(min(necessity, 1.0), 0.0), 4),
        "sufficiency": round(max(min(sufficiency, 1.0), 0.0), 4),
        "edges_retained": round(retained, 4),
        "self_feature_share": round(self_share, 4),
        "neighbourhood_share": round(max(0.0, 1.0 - self_share), 4),
        "n_subgraph_nodes": int(nodes.size),
        "n_subgraph_edges": int(edge_mask_shape.sum()),
        "edges": [e.to_dict() for e in edges],
        "subgraph_nodes": [str(graph.index[v]) for v in nodes],
    }


__all__ = ["explain_node", "EdgeAttribution"]
