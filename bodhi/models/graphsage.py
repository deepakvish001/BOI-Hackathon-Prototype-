"""Layer 5 - GraphSAGE over the financial graph, implemented in NumPy.

Why hand-rolled instead of PyTorch Geometric? Three reasons that matter for a
bank prototype: the whole engine installs from four wheels and runs on a laptop
CPU, there is no GPU/CUDA surface to certify, and every gradient is written out
explicitly so it can be audited. ``tests/test_gradients.py`` checks each
analytic gradient against central finite differences, so "hand-rolled" does not
mean "unverified".

The architecture is the mean-aggregator variant from Hamilton et al. (2017):

    h_v^k = ReLU( W_self^k h_v^(k-1) + W_neigh^k * MEAN_{u in N(v)} h_u^(k-1) )

Neighbour sampling is implemented as a deterministic top-k fan-out on edge
weight when the adjacency is built, which keeps inference reproducible - an
investigator who re-opens a case tomorrow must see the same score.

Because the weights never index a node id, the model is genuinely inductive: an
account opened this morning is scored from its neighbourhood alone.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import scipy.sparse as sp

from bodhi.config import GraphSAGEConfig, MODELS
from bodhi.graph.builder import FinancialGraph


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return np.where(z >= 0, 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60))),
                    np.exp(np.clip(z, -60, 60)) / (1.0 + np.exp(np.clip(z, -60, 60))))


def build_propagation(
    graph: FinancialGraph, fanout: int, weight_power: float = 1.0
) -> sp.csr_matrix:
    """Row-stochastic neighbour aggregator with a top-``fanout`` cap.

    Capping is the sampling step: a node with 4 000 counterparties contributes
    its 15 strongest edges, so a payment hub cannot swamp the message passing.
    """
    n = graph.n_nodes
    rows, cols, vals = [], [], []
    indptr, indices, weights = graph.indptr, graph.indices, graph.weights
    for v in range(n):
        a, b = indptr[v], indptr[v + 1]
        if a == b:
            continue
        nbr = indices[a:b]
        w = weights[a:b] ** weight_power
        if b - a > fanout:
            keep = np.argpartition(-w, fanout - 1)[:fanout]
            nbr, w = nbr[keep], w[keep]
        s = w.sum()
        if s <= 0:
            continue
        rows.append(np.full(nbr.size, v))
        cols.append(nbr)
        vals.append(w / s)
    if not rows:
        return sp.csr_matrix((n, n))
    return sp.csr_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
        shape=(n, n),
    )


class _Adam:
    def __init__(self, params: dict[str, np.ndarray], lr: float, wd: float = 0.0):
        self.lr, self.wd = lr, wd
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}
        self.t = 0

    def step(self, params: dict[str, np.ndarray], grads: dict[str, np.ndarray],
             b1: float = 0.9, b2: float = 0.999, eps: float = 1e-8) -> None:
        self.t += 1
        for k, g in grads.items():
            g = g + self.wd * params[k]
            self.m[k] = b1 * self.m[k] + (1 - b1) * g
            self.v[k] = b2 * self.v[k] + (1 - b2) * g * g
            mh = self.m[k] / (1 - b1 ** self.t)
            vh = self.v[k] / (1 - b2 ** self.t)
            params[k] -= self.lr * mh / (np.sqrt(vh) + eps)


class GraphSAGE:
    """Two-layer mean-aggregator GraphSAGE with a linear readout."""

    def __init__(self, config: GraphSAGEConfig | None = None):
        self.config = config or MODELS.graphsage
        self.params: dict[str, np.ndarray] = {}
        self.mu: np.ndarray | None = None
        self.sigma: np.ndarray | None = None
        self.P: list[sp.csr_matrix] = []
        self.PT: list[sp.csr_matrix] = []
        self.history: list[dict] = []

    # -- initialisation --------------------------------------------------

    def _init_params(self, d_in: int, rng: np.random.Generator) -> None:
        dims = [d_in, *self.config.hidden_dims]
        self.params = {}
        for k in range(len(self.config.hidden_dims)):
            fan_in, fan_out = dims[k], dims[k + 1]
            lim = np.sqrt(6.0 / (fan_in + fan_out))
            self.params[f"Ws{k}"] = rng.uniform(-lim, lim, (fan_in, fan_out))
            self.params[f"Wn{k}"] = rng.uniform(-lim, lim, (fan_in, fan_out))
            self.params[f"b{k}"] = np.zeros(fan_out)
        lim = np.sqrt(6.0 / (dims[-1] + 1))
        self.params["w"] = rng.uniform(-lim, lim, dims[-1])
        self.params["b_out"] = np.zeros(1)

    # -- forward / backward ----------------------------------------------

    def _forward(self, X: np.ndarray, training: bool,
                 rng: np.random.Generator | None = None,
                 P: list[sp.csr_matrix] | None = None) -> dict:
        P = P if P is not None else self.P
        cache: dict = {"H": [X], "Z": [], "A": [], "D": []}
        H = X
        for k in range(len(self.config.hidden_dims)):
            A = P[k] @ H
            Z = H @ self.params[f"Ws{k}"] + A @ self.params[f"Wn{k}"] + self.params[f"b{k}"]
            Hn = np.maximum(Z, 0.0)
            if training and self.config.dropout > 0 and rng is not None:
                mask = (rng.random(Hn.shape) >= self.config.dropout).astype(np.float64)
                mask /= (1.0 - self.config.dropout)
                Hn = Hn * mask
            else:
                mask = None
            cache["A"].append(A)
            cache["Z"].append(Z)
            cache["D"].append(mask)
            cache["H"].append(Hn)
            H = Hn
        logits = H @ self.params["w"] + self.params["b_out"][0]
        cache["logits"] = logits
        return cache

    def _backward(self, cache: dict, dlogits: np.ndarray,
                  P: list[sp.csr_matrix] | None = None,
                  PT: list[sp.csr_matrix] | None = None) -> dict[str, np.ndarray]:
        P = P if P is not None else self.P
        PT = PT if PT is not None else self.PT
        L = len(self.config.hidden_dims)
        grads: dict[str, np.ndarray] = {}
        H_last = cache["H"][L]
        grads["w"] = H_last.T @ dlogits
        grads["b_out"] = np.array([dlogits.sum()])
        dH = np.outer(dlogits, self.params["w"])

        for k in range(L - 1, -1, -1):
            if cache["D"][k] is not None:
                dH = dH * cache["D"][k]
            dZ = dH * (cache["Z"][k] > 0)
            H_prev = cache["H"][k]
            grads[f"Ws{k}"] = H_prev.T @ dZ
            grads[f"Wn{k}"] = cache["A"][k].T @ dZ
            grads[f"b{k}"] = dZ.sum(axis=0)
            dH = dZ @ self.params[f"Ws{k}"].T + PT[k] @ (dZ @ self.params[f"Wn{k}"].T)
        return grads

    # -- training ---------------------------------------------------------

    def fit(
        self,
        graph: FinancialGraph,
        X: np.ndarray,
        y: np.ndarray,
        train_idx: np.ndarray,
        val_idx: np.ndarray | None = None,
        verbose: bool = False,
    ) -> "GraphSAGE":
        c = self.config
        rng = np.random.default_rng(c.random_state)

        X = np.asarray(X, dtype=np.float64)
        self.mu = X[train_idx].mean(axis=0)
        self.sigma = X[train_idx].std(axis=0) + 1e-6
        Xs = (X - self.mu) / self.sigma
        Xs = np.clip(Xs, -8, 8)

        fanouts = list(c.fanouts)
        while len(fanouts) < len(c.hidden_dims):
            fanouts.append(fanouts[-1])
        self.P = [build_propagation(graph, f) for f in fanouts[: len(c.hidden_dims)]]
        self.PT = [p.T.tocsr() for p in self.P]

        self._init_params(Xs.shape[1], rng)
        opt = _Adam(self.params, c.lr, c.weight_decay)

        y = np.asarray(y, dtype=np.float64)
        pos = max(float(y[train_idx].sum()), 1.0)
        neg = max(float(len(train_idx) - pos), 1.0)
        pos_weight = min(neg / pos, 15.0)

        best_val, best_params, patience = -np.inf, None, 0
        for epoch in range(c.epochs):
            batch = rng.permutation(train_idx)[: max(c.batch_size, 1)]
            cache = self._forward(Xs, training=True, rng=rng)
            p = _sigmoid(cache["logits"])

            dlogits = np.zeros_like(p)
            w_b = np.where(y[batch] > 0.5, pos_weight, 1.0)
            dlogits[batch] = w_b * (p[batch] - y[batch]) / batch.size

            grads = self._backward(cache, dlogits)
            opt.step(self.params, grads)

            if val_idx is not None and (epoch % 2 == 0 or epoch == c.epochs - 1):
                pv = self.predict_proba_precomputed(Xs)[val_idx]
                score = _auc(y[val_idx], pv)
                self.history.append({"epoch": epoch, "val_auc": float(score)})
                if score > best_val + 1e-5:
                    best_val, patience = score, 0
                    best_params = {k: v.copy() for k, v in self.params.items()}
                else:
                    patience += 1
                    if patience >= 12:
                        break
                if verbose and epoch % 10 == 0:
                    print(f"  graphsage epoch {epoch:3d}  val_auc={score:.4f}")
        if best_params is not None:
            self.params = best_params
        return self

    # -- inference ---------------------------------------------------------

    def predict_proba_precomputed(self, Xs: np.ndarray) -> np.ndarray:
        return _sigmoid(self._forward(Xs, training=False)["logits"])

    def _standardise(self, X: np.ndarray) -> np.ndarray:
        return np.clip((np.asarray(X, dtype=np.float64) - self.mu) / self.sigma, -8, 8)

    def predict_proba(self, X: np.ndarray,
                      graph: FinancialGraph | None = None) -> np.ndarray:
        """Score every node. Pass ``graph`` to rebuild propagation for new data."""
        if graph is not None:
            fanouts = list(self.config.fanouts)
            while len(fanouts) < len(self.config.hidden_dims):
                fanouts.append(fanouts[-1])
            self.P = [build_propagation(graph, f)
                      for f in fanouts[: len(self.config.hidden_dims)]]
            self.PT = [p.T.tocsr() for p in self.P]
        return self.predict_proba_precomputed(self._standardise(X))

    def embeddings(self, X: np.ndarray) -> np.ndarray:
        cache = self._forward(self._standardise(X), training=False)
        return cache["H"][-1]

    # -- persistence -------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path.with_suffix(".npz"),
            mu=self.mu, sigma=self.sigma,
            hidden_dims=np.array(self.config.hidden_dims),
            fanouts=np.array(self.config.fanouts),
            **self.params,
        )

    @classmethod
    def load(cls, path: Path, config: GraphSAGEConfig | None = None) -> "GraphSAGE":
        data = np.load(Path(path).with_suffix(".npz"))
        cfg = config or GraphSAGEConfig(
            hidden_dims=tuple(int(x) for x in data["hidden_dims"]),
            fanouts=tuple(int(x) for x in data["fanouts"]),
        )
        obj = cls(cfg)
        obj.mu, obj.sigma = data["mu"], data["sigma"]
        obj.params = {k: data[k] for k in data.files
                      if k not in ("mu", "sigma", "hidden_dims", "fanouts")}
        return obj


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    """ROC-AUC via rank statistics (avoids a sklearn import in the hot loop)."""
    y = np.asarray(y) > 0.5
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos == 0 or n_neg == 0:
        return 0.5
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, len(p) + 1)
    # average ranks over ties
    sp_ = np.sort(p)
    i = 0
    while i < len(sp_):
        j = i
        while j + 1 < len(sp_) and sp_[j + 1] == sp_[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


__all__ = ["GraphSAGE", "build_propagation"]
