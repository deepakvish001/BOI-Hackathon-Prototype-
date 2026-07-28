"""Fraud-ring extraction and money-flow tracing.

An account-level score tells an investigator *who* to look at. A ring tells
them *what they are looking at* - and that is the difference between closing
one account and dismantling a syndicate.

Ring extraction runs on the sub-graph induced by high-risk accounts plus their
immediate neighbourhood, then applies Louvain community detection. Working on
the induced sub-graph rather than the full graph keeps it fast enough to run
inside an API request.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from bodhi.graph.builder import EDGE_TYPES, FinancialGraph

#: Communities larger than this are payment infrastructure, not a ring.
MAX_RING_SIZE = 60
MIN_RING_SIZE = 3

#: A community only counts as a ring if this share of its members were
#: independently flagged. Without it, one flagged account inside a Business
#: Correspondent's device cluster drags forty innocent village customers into a
#: "51-account device farm" - which is precisely the kind of confident nonsense
#: that gets an AML system switched off.
MIN_FLAGGED_SHARE = 0.35

#: And the flagged members themselves have to be convincing on average.
MIN_RING_MEAN_RISK = 50.0


@dataclass
class Ring:
    """A candidate syndicate.

    ``members`` holds only the accounts that were independently flagged.
    Accounts merely adjacent to them - typically via a shared handset - are
    reported separately as ``associated``. Collapsing the two would put
    innocent people on a list titled "fraud ring members", which is exactly the
    kind of output that gets an AML system distrusted and then switched off.
    """

    ring_id: str
    members: list[str]
    associated: list[str]
    size: int
    density: float
    total_amount: float
    shared_devices: list[str]
    mean_risk: float
    max_risk: float
    typology_hint: str
    cashout_accounts: list[str]
    collector_accounts: list[str]

    @property
    def footprint(self) -> int:
        """Flagged members plus everyone linked to them."""
        return self.size + len(self.associated)

    def to_dict(self) -> dict:
        return {
            "ring_id": self.ring_id,
            "members": self.members,
            "associated": self.associated,
            "size": self.size,
            "footprint": self.footprint,
            "density": round(self.density, 4),
            "total_amount": round(self.total_amount, 2),
            "shared_devices": self.shared_devices,
            "mean_risk": round(self.mean_risk, 2),
            "max_risk": round(self.max_risk, 2),
            "typology_hint": self.typology_hint,
            "cashout_accounts": self.cashout_accounts,
            "collector_accounts": self.collector_accounts,
        }


def _induced_subgraph(graph: FinancialGraph, seeds: np.ndarray,
                      expand: bool = True) -> nx.Graph:
    """Build a NetworkX view of the seeds and (optionally) their neighbours."""
    nodes = set(int(s) for s in seeds)
    if expand:
        for s in seeds:
            nbrs, _, et = graph.neighbours(int(s))
            # Only hardware co-location pulls in an unflagged account. Money
            # edges are *not* expanded: every victim who paid a collector would
            # otherwise be dragged into the ring and reported as a mule.
            nodes.update(int(x) for x in nbrs[et == EDGE_TYPES["DEVICE"]])
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for s in nodes:
        nbrs, w, et = graph.neighbours(int(s))
        for t, weight, kind in zip(nbrs, w, et):
            t = int(t)
            if t in nodes and s < t:
                g.add_edge(s, t, weight=float(weight), etype=int(kind))
    return g


def detect_rings(
    graph: FinancialGraph,
    risk: pd.Series,
    threshold: float = 55.0,
    max_seeds: int = 2_000,
    resolution: float = 4.0,
    seed: int = 42,
) -> list[Ring]:
    """Group high-risk accounts into candidate syndicates.

    Parameters
    ----------
    risk
        Account-level 0-100 risk, indexed by ``account_id``.
    threshold
        Only accounts at or above this score seed a ring.
    resolution
        Louvain resolution. Tuned high (4.0) on purpose: at the default of 1.0
        the algorithm merges every flagged account in the bank into one or two
        giant communities, which recovers no rings at all. Higher resolution
        favours many small dense communities, which is what a syndicate
        actually looks like.
    """
    scores = risk.reindex(graph.index).fillna(0.0).to_numpy()
    seeds = np.flatnonzero(scores >= threshold)
    if seeds.size == 0:
        return []
    if seeds.size > max_seeds:
        seeds = seeds[np.argsort(-scores[seeds])[:max_seeds]]

    g = _induced_subgraph(graph, seeds)
    if g.number_of_nodes() == 0:
        return []

    try:
        communities = nx.community.louvain_communities(
            g, weight="weight", resolution=resolution, seed=seed
        )
    except Exception:  # pragma: no cover - tiny/degenerate graphs
        communities = [set(c) for c in nx.connected_components(g)]

    money = graph.money_matrix()
    seed_set = set(int(s) for s in seeds)
    rings: list[Ring] = []

    for k, comm in enumerate(sorted(communities, key=len, reverse=True)):
        members = sorted(comm)
        flagged = [m for m in members if m in seed_set]
        if len(flagged) < MIN_RING_SIZE or len(members) > MAX_RING_SIZE:
            continue
        if len(flagged) / len(members) < MIN_FLAGGED_SHARE:
            continue
        if float(scores[np.array(flagged)].mean()) < MIN_RING_MEAN_RISK:
            continue

        sub = g.subgraph(flagged)
        possible = len(flagged) * (len(flagged) - 1) / 2
        density = sub.number_of_edges() / possible if possible else 0.0

        idx = np.array(flagged)
        total = float(money[idx][:, idx].sum())

        devices: set[str] = set()
        for m in flagged:
            devices.update(graph.account_devices.get(graph.index[m], []))
        shared = sorted(
            d for d in devices
            if sum(1 for m in flagged if d in graph.account_devices.get(graph.index[m], [])) > 1
        )

        member_ids = [str(graph.index[m]) for m in flagged]
        associated_ids = [str(graph.index[m]) for m in members if m not in seed_set]
        # Mean risk is reported over the *flagged* members: averaging in the
        # unflagged accounts pulled in by a shared device would understate a
        # real ring and flatter a spurious one.
        member_risk = scores[np.array(flagged)]

        f_idx = np.array(flagged)
        in_amt = np.asarray(money[:, f_idx].sum(axis=0)).ravel()
        out_amt = np.asarray(money[f_idx].sum(axis=1)).ravel()
        # Collectors take in far more than they push on; cash-out nodes are the
        # opposite and additionally drain to the CASH_POOL sink.
        collectors = [member_ids[i] for i in np.argsort(-(in_amt - out_amt))[:3]
                      if in_amt[i] > 0]
        cashouts = [member_ids[i] for i in np.argsort(-(out_amt - in_amt))[:3]
                    if out_amt[i] > 0]

        rings.append(Ring(
            ring_id=f"BODHI-RING-{k + 1:04d}",
            members=member_ids,
            associated=associated_ids,
            size=len(flagged),
            density=float(density),
            total_amount=total,
            shared_devices=shared[:10],
            mean_risk=float(member_risk.mean()),
            max_risk=float(member_risk.max()),
            typology_hint=_typology_hint(sub, shared, in_amt, out_amt, density),
            cashout_accounts=cashouts,
            collector_accounts=collectors,
        ))

    rings.sort(key=lambda r: (r.mean_risk * r.size), reverse=True)
    return rings


def _typology_hint(sub: nx.Graph, shared_devices: list[str],
                   in_amt: np.ndarray, out_amt: np.ndarray,
                   density: float) -> str:
    """Name the shape of the ring so the investigator knows what to expect."""
    if shared_devices:
        return "DEVICE_FARM"
    degrees = [d for _, d in sub.degree()]
    if not degrees:
        return "UNKNOWN"
    max_deg = max(degrees)
    if max_deg >= 0.6 * (len(degrees) - 1) and len(degrees) > 4:
        return "FAN_IN_AGGREGATION"
    if density < 0.35 and nx.is_connected(sub) and max_deg <= 3:
        return "LAYERING_CHAIN"
    concentration = float(np.max(out_amt) / max(np.sum(out_amt), 1.0))
    if concentration > 0.5:
        return "RAPID_CASHOUT"
    return "MULE_CLUSTER"


def ring_for_account(rings: list[Ring], account_id: str) -> Ring | None:
    for r in rings:
        if account_id in r.members:
            return r
    return None


# --------------------------------------------------------------------------
# temporal flow tracing
# --------------------------------------------------------------------------


def trace_flows(
    graph: FinancialGraph,
    account_id: str,
    max_hops: int = 4,
    max_paths: int = 25,
    window_hours: float = 48.0,
    direction: str = "forward",
) -> list[dict]:
    """Follow the money.

    Returns *time-respecting* paths: each hop must occur after the previous one
    and within ``window_hours``. A path that ignores time is meaningless for
    laundering - money cannot be forwarded before it arrives.
    """
    start = graph.code(account_id)
    if start < 0:
        return []
    window = window_hours * 3_600.0

    forward = direction == "forward"
    src, dst = (graph.src, graph.dst) if forward else (graph.dst, graph.src)
    order = np.lexsort((graph.ts, src))
    s_sorted, d_sorted = src[order], dst[order]
    ts_sorted, amt_sorted = graph.ts[order], graph.amount[order]
    id_sorted = graph.txn_id[order]
    starts = np.searchsorted(s_sorted, np.arange(graph.n_nodes), side="left")
    ends = np.searchsorted(s_sorted, np.arange(graph.n_nodes), side="right")

    paths: list[dict] = []
    # (node, arrival time, amount, hop list)
    stack: list[tuple[int, float, float, list[dict]]] = [(start, -np.inf, 0.0, [])]

    while stack and len(paths) < max_paths:
        node, t_arrive, amt, hops = stack.pop()
        if len(hops) >= max_hops:
            paths.append(_finish(graph, hops))
            continue
        a, b = starts[node], ends[node]
        if a == b:
            if hops:
                paths.append(_finish(graph, hops))
            continue
        cand = np.arange(a, b)
        if np.isfinite(t_arrive):
            cand = cand[(ts_sorted[a:b] > t_arrive) &
                        (ts_sorted[a:b] <= t_arrive + window)]
        if cand.size == 0:
            if hops:
                paths.append(_finish(graph, hops))
            continue
        # Follow the largest flows first: that is where the money actually went.
        cand = cand[np.argsort(-amt_sorted[cand])][:3]
        for i in cand:
            nxt = int(d_sorted[i])
            if any(h["to_code"] == nxt for h in hops) or nxt == start:
                continue  # do not loop
            hop = {
                "txn_id": str(id_sorted[i]),
                "from": str(graph.index[node]),
                "to": str(graph.index[nxt]),
                "to_code": nxt,
                "amount": float(amt_sorted[i]),
                "ts": float(ts_sorted[i]),
                "gap_minutes": (float(ts_sorted[i] - t_arrive) / 60.0
                                if np.isfinite(t_arrive) else 0.0),
            }
            stack.append((nxt, float(ts_sorted[i]), float(amt_sorted[i]), hops + [hop]))

    paths.sort(key=lambda p: p["total_amount"], reverse=True)
    return paths[:max_paths]


def _finish(graph: FinancialGraph, hops: list[dict]) -> dict:
    chain = [hops[0]["from"]] + [h["to"] for h in hops]
    gaps = [h["gap_minutes"] for h in hops[1:]]
    return {
        "path": chain,
        "hops": [{k: v for k, v in h.items() if k != "to_code"} for h in hops],
        "n_hops": len(hops),
        "total_amount": float(hops[0]["amount"]),
        "terminal_amount": float(hops[-1]["amount"]),
        "elapsed_minutes": round((hops[-1]["ts"] - hops[0]["ts"]) / 60.0, 1),
        "median_hop_minutes": round(float(np.median(gaps)), 1) if gaps else 0.0,
        # Value retained after each hop's commission: laundering chains leak
        # 2-10% per hop, legitimate chains do not have this shape at all.
        "value_retention": round(float(hops[-1]["amount"] / max(hops[0]["amount"], 1.0)), 4),
    }


__all__ = ["Ring", "detect_rings", "ring_for_account", "trace_flows",
           "MAX_RING_SIZE", "MIN_RING_SIZE"]
