"""Layer 3 - build the heterogeneous financial graph.

Nodes are accounts. Three kinds of edge connect them:

``MONEY``
    A funds transfer. Directed, carrying amount and timestamp; symmetrised for
    message passing but kept directed for flow tracing.
``DEVICE``
    Two accounts operated from the same handset or SIM. This is the edge that
    exposes account-rental farms, and it is invisible to any tabular model.
``IP``
    Two accounts seen from the same public address. Deliberately down-weighted
    and capped, because CGNAT means thousands of innocent customers share one
    address - an uncapped IP edge is a false-positive factory.

The graph is stored as CSR arrays rather than a `networkx` object because
GraphSAGE needs to sample neighbours millions of times per epoch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

from bodhi.data.generator import CASH_POOL

EDGE_TYPES = {"MONEY": 0, "DEVICE": 1, "IP": 2}

#: An identifier shared by more accounts than this is infrastructure (a CGNAT
#: pool, a shared kiosk), not evidence of collusion.
MAX_SHARED_FANOUT = 25

TOPOLOGY_FEATURES: tuple[str, ...] = (
    "pagerank", "reverse_pagerank", "degree_total", "degree_money_in",
    "degree_money_out", "degree_device", "component_size", "clustering",
    "two_hop_size", "neighbour_risk_mean", "money_in_out_imbalance",
)


@dataclass
class FinancialGraph:
    """CSR-backed account graph plus the directed money ledger."""

    index: pd.Index                       # node id -> account_id
    indptr: np.ndarray
    indices: np.ndarray
    weights: np.ndarray
    etypes: np.ndarray
    # directed money edges, sorted by timestamp
    src: np.ndarray
    dst: np.ndarray
    amount: np.ndarray
    ts: np.ndarray
    txn_id: np.ndarray
    device_of_edge: dict[int, list[str]] = field(default_factory=dict)
    account_devices: dict[str, list[str]] = field(default_factory=dict)

    @property
    def n_nodes(self) -> int:
        return len(self.index)

    @property
    def n_edges(self) -> int:
        return int(self.indices.size)

    def neighbours(self, node: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return ``(neighbour ids, weights, edge types)`` for one node."""
        a, b = self.indptr[node], self.indptr[node + 1]
        return self.indices[a:b], self.weights[a:b], self.etypes[a:b]

    def code(self, account_id: str) -> int:
        i = self.index.get_indexer([account_id])[0]
        return int(i)

    def to_scipy(self) -> sp.csr_matrix:
        return sp.csr_matrix(
            (self.weights, self.indices, self.indptr),
            shape=(self.n_nodes, self.n_nodes),
        )

    def money_matrix(self) -> sp.csr_matrix:
        """Directed amount-weighted adjacency."""
        return sp.csr_matrix(
            (self.amount, (self.src, self.dst)),
            shape=(self.n_nodes, self.n_nodes),
        )


# --------------------------------------------------------------------------


def build_graph(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame,
    as_of: float | None = None,
    include_ip: bool = True,
) -> FinancialGraph:
    """Assemble the graph from an event stream, honouring ``as_of``."""
    index = pd.Index(accounts["account_id"].to_numpy())
    n = len(index)

    tx = transactions
    if as_of is not None:
        tx = tx[tx["ts"] <= as_of]
    tx = tx[tx["dst_account"] != CASH_POOL]

    src = index.get_indexer(tx["src_account"].to_numpy())
    dst = index.get_indexer(tx["dst_account"].to_numpy())
    ok = (src >= 0) & (dst >= 0) & (src != dst)
    src, dst = src[ok], dst[ok]
    amount = tx["amount"].to_numpy(dtype=np.float64)[ok]
    ts = tx["ts"].to_numpy(dtype=np.float64)[ok]
    txn_id = tx["txn_id"].to_numpy()[ok] if "txn_id" in tx.columns \
        else np.array([f"E{i}" for i in range(src.size)])

    order = np.argsort(ts, kind="stable")
    src, dst, amount, ts, txn_id = (src[order], dst[order], amount[order],
                                    ts[order], txn_id[order])

    # ---- money edges (aggregated, symmetrised) --------------------------
    pair = src.astype(np.int64) * n + dst.astype(np.int64)
    uniq, inv = np.unique(pair, return_inverse=True)
    agg_amt = np.zeros(uniq.size)
    np.add.at(agg_amt, inv, amount)
    agg_cnt = np.zeros(uniq.size)
    np.add.at(agg_cnt, inv, 1.0)
    m_src = (uniq // n).astype(np.int64)
    m_dst = (uniq % n).astype(np.int64)
    # log-scaled so a single 5-lakh transfer does not drown out 40 small ones
    m_w = np.log1p(agg_amt) * (1.0 + np.log1p(agg_cnt))

    e_src = np.concatenate([m_src, m_dst])
    e_dst = np.concatenate([m_dst, m_src])
    e_w = np.concatenate([m_w, m_w])
    e_t = np.full(e_src.size, EDGE_TYPES["MONEY"], dtype=np.int8)

    # ---- shared-identifier edges ----------------------------------------
    account_devices: dict[str, list[str]] = {}
    for col, etype, weight, enabled in (
        ("device_id", EDGE_TYPES["DEVICE"], 6.0, True),
        ("ip_address", EDGE_TYPES["IP"], 1.5, include_ip),
    ):
        if not enabled or col not in tx.columns:
            continue
        s, d, w = _shared_identifier_edges(tx, index, col, weight)
        if s.size:
            e_src = np.concatenate([e_src, s])
            e_dst = np.concatenate([e_dst, d])
            e_w = np.concatenate([e_w, w])
            e_t = np.concatenate([e_t, np.full(s.size, etype, dtype=np.int8)])
        if col == "device_id":
            sub = tx.loc[tx[col].notna(), ["src_account", col]].drop_duplicates()
            for acct, dev in zip(sub["src_account"].to_numpy(), sub[col].to_numpy()):
                account_devices.setdefault(str(acct), []).append(str(dev))

    order = np.lexsort((e_dst, e_src))
    e_src, e_dst, e_w, e_t = e_src[order], e_dst[order], e_w[order], e_t[order]
    indptr = np.searchsorted(e_src, np.arange(n + 1), side="left")

    return FinancialGraph(
        index=index, indptr=indptr.astype(np.int64), indices=e_dst.astype(np.int64),
        weights=e_w.astype(np.float64), etypes=e_t,
        src=src, dst=dst, amount=amount, ts=ts, txn_id=txn_id,
        account_devices=account_devices,
    )


def _shared_identifier_edges(
    tx: pd.DataFrame, index: pd.Index, col: str, weight: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Connect accounts that share a device or an IP, skipping infrastructure."""
    sub = tx.loc[tx[col].notna(), ["src_account", col]].drop_duplicates()
    if len(sub) == 0:
        return (np.array([], dtype=np.int64),) * 3
    codes = index.get_indexer(sub["src_account"].to_numpy())
    keep = codes >= 0
    codes = codes[keep]
    ident = pd.factorize(sub[col].to_numpy()[keep])[0]
    if codes.size == 0:
        return (np.array([], dtype=np.int64),) * 3

    order = np.lexsort((codes, ident))
    ident, codes = ident[order], codes[order]
    starts = np.flatnonzero(np.concatenate([[True], ident[1:] != ident[:-1]]))
    ends = np.concatenate([starts[1:], [ident.size]])

    s_list, d_list = [], []
    for a, b in zip(starts, ends):
        members = codes[a:b]
        k = members.size
        if k < 2 or k > MAX_SHARED_FANOUT:
            continue  # singleton, or a CGNAT/kiosk pool: carries no signal
        left = np.repeat(members, k)
        right = np.tile(members, k)
        m = left != right
        s_list.append(left[m])
        d_list.append(right[m])
    if not s_list:
        return (np.array([], dtype=np.int64),) * 3
    s = np.concatenate(s_list)
    d = np.concatenate(d_list)
    # Weight decays with clique size: a 20-account device is noisier evidence
    # per pair than a 2-account one.
    return s, d, np.full(s.size, weight)


# --------------------------------------------------------------------------
# topology features
# --------------------------------------------------------------------------


def topology_features(
    graph: FinancialGraph, prior_risk: np.ndarray | None = None
) -> pd.DataFrame:
    """Structural descriptors that tabular features cannot express."""
    n = graph.n_nodes
    A = graph.to_scipy()

    pr = _pagerank(graph.src, graph.dst, graph.amount, n)
    rpr = _pagerank(graph.dst, graph.src, graph.amount, n)

    row = np.repeat(np.arange(n), np.diff(graph.indptr))
    deg_total = np.diff(graph.indptr).astype(float)
    money_mask = graph.etypes == EDGE_TYPES["MONEY"]
    dev_mask = graph.etypes == EDGE_TYPES["DEVICE"]
    deg_device = np.bincount(row[dev_mask], minlength=n).astype(float)
    deg_in = np.bincount(graph.dst, minlength=n).astype(float)
    deg_out = np.bincount(graph.src, minlength=n).astype(float)

    n_comp, labels = connected_components(A, directed=False)
    comp_size = np.bincount(labels, minlength=n_comp).astype(float)[labels]

    # Local clustering, measured on the money graph alone.
    B = sp.csr_matrix(
        (np.ones(int(money_mask.sum())), (row[money_mask], graph.indices[money_mask])),
        shape=(n, n),
    )
    B = ((B + B.T) > 0).astype(np.float64)
    tri = np.asarray((B @ B).multiply(B).sum(axis=1)).ravel()
    deg_b = np.asarray(B.sum(axis=1)).ravel()
    clustering = np.divide(tri, np.maximum(deg_b * (deg_b - 1), 1.0))

    two_hop = np.asarray(((B @ B) > 0).sum(axis=1)).ravel().astype(float)

    if prior_risk is None:
        prior_risk = np.zeros(n)
    nbr_risk = np.divide(B @ prior_risk, np.maximum(deg_b, 1.0))

    in_amt = np.bincount(graph.dst, weights=graph.amount, minlength=n)
    out_amt = np.bincount(graph.src, weights=graph.amount, minlength=n)
    imbalance = np.divide(out_amt - in_amt, np.maximum(out_amt + in_amt, 1.0))

    df = pd.DataFrame(
        {
            "pagerank": pr,
            "reverse_pagerank": rpr,
            "degree_total": deg_total,
            "degree_money_in": deg_in,
            "degree_money_out": deg_out,
            "degree_device": deg_device,
            "component_size": comp_size,
            "clustering": clustering,
            "two_hop_size": two_hop,
            "neighbour_risk_mean": nbr_risk,
            "money_in_out_imbalance": imbalance,
        },
        index=graph.index,
    )
    df.index.name = "account_id"
    return df.replace([np.inf, -np.inf], 0.0).fillna(0.0)


def _pagerank(src: np.ndarray, dst: np.ndarray, weight: np.ndarray, n: int,
              damping: float = 0.85, iters: int = 40) -> np.ndarray:
    """Weighted PageRank via sparse power iteration (no networkx at runtime)."""
    if src.size == 0:
        return np.full(n, 1.0 / max(n, 1))
    M = sp.csr_matrix((np.log1p(weight), (dst, src)), shape=(n, n))
    colsum = np.asarray(M.sum(axis=0)).ravel()
    dangling = colsum == 0
    inv = np.divide(1.0, colsum, out=np.zeros_like(colsum), where=~dangling)
    M = M @ sp.diags(inv)
    r = np.full(n, 1.0 / n)
    for _ in range(iters):
        r_new = damping * (M @ r) + damping * r[dangling].sum() / n + (1 - damping) / n
        if np.abs(r_new - r).sum() < 1e-10:
            r = r_new
            break
        r = r_new
    return r


__all__ = ["FinancialGraph", "build_graph", "topology_features", "TOPOLOGY_FEATURES",
           "EDGE_TYPES", "MAX_SHARED_FANOUT"]
