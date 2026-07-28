"""Layer 6 - Temporal Graph Network memory over each account's event stream.

TGN (Rossi et al., 2020) keeps a per-node memory vector updated by a GRU every
time the node is involved in an interaction, with the elapsed time injected
through a learnable time encoding. That memory module is what this file
implements, in NumPy, with backpropagation through time written out by hand.

Why it earns its place next to XGBoost and GraphSAGE: the tabular model sees
*aggregates* ("70% of outflow left within an hour") and the graph model sees
*structure* ("these six accounts form a clique"). Neither can see **order**.
Order is what separates a shopkeeper who receives forty payments a day and
spends them over the week from a collection mule who receives forty payments in
ninety minutes and empties the account at 03:12. Same daily totals, same
counterparty count, completely different sequence.

Sequences are right-aligned and front-padded, so the final hidden state is
always the account's most recent memory.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from bodhi.config import CASHOUT_CHANNELS, FEATURES, MODELS, REGULATORY, TemporalConfig
from bodhi.data.generator import CASH_POOL

_KEY = 2_000_000_000

#: Names of the per-event input channels, in order. Used by the explainer.
EVENT_FEATURES: tuple[str, ...] = (
    "log_amount", "direction", "log_gap", "is_cashout", "is_night",
    "fresh_beneficiary", "novel_counterparty",
    "ch_upi", "ch_imps", "ch_neft_rtgs", "ch_atm_aeps", "ch_card",
    "amount_vs_typical",
)
N_EVENT_FEATURES = len(EVENT_FEATURES)


def build_sequences(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame,
    max_events: int | None = None,
    as_of: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Turn the ledger into ``(n_accounts, T, F)`` right-aligned event tensors."""
    T = max_events or MODELS.temporal.max_events
    index = pd.Index(accounts["account_id"].to_numpy())
    n = len(index)

    tx = transactions
    if as_of is not None:
        tx = tx[tx["ts"] <= as_of]
    if len(tx) == 0:
        return np.zeros((n, T, N_EVENT_FEATURES)), np.zeros((n, T))

    src = index.get_indexer(tx["src_account"].to_numpy())
    dst = index.get_indexer(tx["dst_account"].to_numpy())
    ts = tx["ts"].to_numpy(dtype=np.float64)
    amt = tx["amount"].to_numpy(dtype=np.float64)
    chan = tx["channel"].to_numpy()
    ben = (tx["beneficiary_age_hours"].to_numpy(dtype=np.float64)
           if "beneficiary_age_hours" in tx.columns else np.full(len(tx), np.nan))

    # One row per (account, event): a transfer is an event for both parties.
    out_mask, in_mask = src >= 0, dst >= 0
    code = np.concatenate([src[out_mask], dst[in_mask]])
    e_ts = np.concatenate([ts[out_mask], ts[in_mask]])
    e_amt = np.concatenate([amt[out_mask], amt[in_mask]])
    e_dir = np.concatenate([-np.ones(int(out_mask.sum())), np.ones(int(in_mask.sum()))])
    e_chan = np.concatenate([chan[out_mask], chan[in_mask]])
    e_ben = np.concatenate([ben[out_mask], ben[in_mask]])
    e_cp = np.concatenate([dst[out_mask], src[in_mask]])
    e_cash = np.isin(e_chan, list(CASHOUT_CHANNELS)) | np.concatenate([
        (tx["dst_account"].to_numpy()[out_mask] == CASH_POOL),
        np.zeros(int(in_mask.sum()), dtype=bool),
    ])

    order = np.lexsort((e_ts, code))
    code, e_ts, e_amt, e_dir = code[order], e_ts[order], e_amt[order], e_dir[order]
    e_chan, e_ben, e_cp, e_cash = e_chan[order], e_ben[order], e_cp[order], e_cash[order]

    starts = np.searchsorted(code, np.arange(n), side="left")
    ends = np.searchsorted(code, np.arange(n), side="right")
    counts = ends - starts

    # Inter-event gap within each account (first event of an account -> 0).
    gap = np.zeros_like(e_ts)
    same = np.concatenate([[False], code[1:] == code[:-1]])
    gap[same] = np.diff(e_ts)[same[1:]]
    gap = np.maximum(gap, 0.0)

    # Has this account transacted with this counterparty before?
    key = code.astype(np.int64) * _KEY + np.maximum(e_cp, 0).astype(np.int64)
    novel = (~pd.Series(key).duplicated().to_numpy()).astype(float)

    hours = ((e_ts % 86_400) / 3_600).astype(int)
    lo_h, hi_h = FEATURES.night_hours
    is_night = ((hours >= lo_h) & (hours <= hi_h)).astype(float)
    fresh = (np.nan_to_num(e_ben, nan=9_999.0)
             < REGULATORY.fresh_beneficiary_hours).astype(float)

    typical = np.zeros(n)
    np.add.at(typical, code, e_amt)
    typical = typical / np.maximum(counts, 1)
    amt_ratio = np.log1p(e_amt) - np.log1p(np.maximum(typical[code], 1.0))

    feats = np.zeros((code.size, N_EVENT_FEATURES))
    feats[:, 0] = np.log1p(e_amt) / 10.0
    feats[:, 1] = e_dir
    feats[:, 2] = np.log1p(gap) / 12.0
    feats[:, 3] = e_cash.astype(float)
    feats[:, 4] = is_night
    feats[:, 5] = fresh
    feats[:, 6] = novel
    feats[:, 7] = (e_chan == "UPI").astype(float)
    feats[:, 8] = (e_chan == "IMPS").astype(float)
    feats[:, 9] = np.isin(e_chan, ("NEFT", "RTGS")).astype(float)
    feats[:, 10] = np.isin(e_chan, ("ATM", "AEPS")).astype(float)
    feats[:, 11] = np.isin(e_chan, ("CARD", "WALLET")).astype(float)
    feats[:, 12] = np.clip(amt_ratio, -6, 6)

    # Keep the most recent T events, right-aligned.
    pos = np.arange(code.size) - starts[code]
    from_end = counts[code] - 1 - pos
    keep = from_end < T
    slot = (T - 1 - from_end[keep]).astype(int)

    X = np.zeros((n, T, N_EVENT_FEATURES))
    M = np.zeros((n, T))
    X[code[keep], slot] = feats[keep]
    M[code[keep], slot] = 1.0
    return X, M


# --------------------------------------------------------------------------


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))


class TemporalGraphNetwork:
    """GRU memory + learnable time encoding + linear readout."""

    def __init__(self, config: TemporalConfig | None = None):
        self.config = config or MODELS.temporal
        self.params: dict[str, np.ndarray] = {}
        self.mu: np.ndarray | None = None
        self.sigma: np.ndarray | None = None
        self.history: list[dict] = []

    # -- parameters --------------------------------------------------------

    def _init(self, rng: np.random.Generator) -> None:
        c = self.config
        F = N_EVENT_FEATURES + c.time_dim
        H = c.hidden_dim
        def glorot(a, b):
            lim = np.sqrt(6.0 / (a + b))
            return rng.uniform(-lim, lim, (a, b))
        self.params = {
            "Wz": glorot(F, H), "Wr": glorot(F, H), "Wn": glorot(F, H),
            "Uz": glorot(H, H), "Ur": glorot(H, H), "Un": glorot(H, H),
            "bz": np.zeros(H), "br": np.zeros(H), "bn": np.zeros(H),
            # time2vec-style encoder over the log inter-event gap
            "tf": np.geomspace(0.2, 12.0, c.time_dim) * rng.uniform(0.8, 1.2, c.time_dim),
            "tp": rng.uniform(0, 2 * np.pi, c.time_dim),
            "Wo": glorot(H, H // 2), "bo": np.zeros(H // 2),
            "w": glorot(H // 2, 1).ravel(), "b_out": np.zeros(1),
        }

    # -- forward ----------------------------------------------------------

    def _time_encode(self, gap: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``sin(f_k * u + p_k)`` over the normalised gap; returns (enc, phase)."""
        phase = gap[..., None] * self.params["tf"] + self.params["tp"]
        return np.sin(phase), phase

    def _forward(self, X: np.ndarray, M: np.ndarray, training: bool = False) -> dict:
        B, T, _ = X.shape
        H = self.config.hidden_dim
        gap = X[:, :, 2]                       # already log-scaled
        enc, phase = self._time_encode(gap)
        Xf = np.concatenate([X, enc], axis=2)

        h = np.zeros((B, H))
        cache = {"z": [], "r": [], "nt": [], "g": [], "h_prev": [], "Xf": Xf,
                 "phase": phase, "gap": gap, "M": M}
        p = self.params
        for t in range(T):
            xt = Xf[:, t, :]
            m = M[:, t][:, None]
            z = _sigmoid(xt @ p["Wz"] + h @ p["Uz"] + p["bz"])
            r = _sigmoid(xt @ p["Wr"] + h @ p["Ur"] + p["br"])
            g = h @ p["Un"]
            nt = np.tanh(xt @ p["Wn"] + r * g + p["bn"])
            h_new = (1.0 - z) * nt + z * h
            cache["z"].append(z); cache["r"].append(r)
            cache["nt"].append(nt); cache["g"].append(g); cache["h_prev"].append(h)
            h = m * h_new + (1.0 - m) * h
        o = np.tanh(h @ p["Wo"] + p["bo"])
        logits = o @ p["w"] + p["b_out"][0]
        cache["h_final"] = h
        cache["o"] = o
        cache["logits"] = logits
        return cache

    # -- backward ----------------------------------------------------------

    def _backward(self, cache: dict, dlogits: np.ndarray) -> dict[str, np.ndarray]:
        p = self.params
        Xf, M = cache["Xf"], cache["M"]
        B, T, F = Xf.shape
        grads = {k: np.zeros_like(v) for k, v in p.items()}

        o = cache["o"]
        grads["w"] = o.T @ dlogits
        grads["b_out"] = np.array([dlogits.sum()])
        do = np.outer(dlogits, p["w"])
        dpre_o = do * (1.0 - o ** 2)
        grads["Wo"] = cache["h_final"].T @ dpre_o
        grads["bo"] = dpre_o.sum(axis=0)
        dh = dpre_o @ p["Wo"].T

        dXf = np.zeros_like(Xf)
        for t in range(T - 1, -1, -1):
            z, r, nt = cache["z"][t], cache["r"][t], cache["nt"][t]
            g, h_prev = cache["g"][t], cache["h_prev"][t]
            xt = Xf[:, t, :]
            m = M[:, t][:, None]

            dh_eff = dh * m
            dh_prev = dh * (1.0 - m)

            dz = dh_eff * (h_prev - nt)
            dnt = dh_eff * (1.0 - z)
            dh_prev = dh_prev + dh_eff * z

            dxn = dnt * (1.0 - nt ** 2)
            grads["Wn"] += xt.T @ dxn
            grads["bn"] += dxn.sum(axis=0)
            dr = dxn * g
            dg = dxn * r
            grads["Un"] += h_prev.T @ dg
            dh_prev = dh_prev + dg @ p["Un"].T

            dxr = dr * r * (1.0 - r)
            grads["Wr"] += xt.T @ dxr
            grads["Ur"] += h_prev.T @ dxr
            grads["br"] += dxr.sum(axis=0)
            dh_prev = dh_prev + dxr @ p["Ur"].T

            dxz = dz * z * (1.0 - z)
            grads["Wz"] += xt.T @ dxz
            grads["Uz"] += h_prev.T @ dxz
            grads["bz"] += dxz.sum(axis=0)
            dh_prev = dh_prev + dxz @ p["Uz"].T

            dXf[:, t, :] = dxz @ p["Wz"].T + dxr @ p["Wr"].T + dxn @ p["Wn"].T
            dh = dh_prev

        # gradients into the learnable time encoding
        denc = dXf[:, :, N_EVENT_FEATURES:]
        cos_phase = np.cos(cache["phase"])
        common = denc * cos_phase
        grads["tf"] = (common * cache["gap"][..., None]).sum(axis=(0, 1))
        grads["tp"] = common.sum(axis=(0, 1))
        return grads

    # -- training -----------------------------------------------------------

    def fit(
        self,
        X: np.ndarray,
        M: np.ndarray,
        y: np.ndarray,
        train_idx: np.ndarray,
        val_idx: np.ndarray | None = None,
        verbose: bool = False,
    ) -> "TemporalGraphNetwork":
        c = self.config
        rng = np.random.default_rng(c.random_state)
        X = np.asarray(X, dtype=np.float64)
        M = np.asarray(M, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)

        flat = X[train_idx].reshape(-1, X.shape[2])
        keep = M[train_idx].reshape(-1) > 0
        self.mu = flat[keep].mean(axis=0) if keep.any() else np.zeros(X.shape[2])
        self.sigma = (flat[keep].std(axis=0) + 1e-6) if keep.any() else np.ones(X.shape[2])
        Xs = self._standardise(X, M)

        self._init(rng)
        from bodhi.models.graphsage import _Adam, _auc
        opt = _Adam(self.params, c.lr, c.weight_decay)

        pos = max(float(y[train_idx].sum()), 1.0)
        neg = max(float(len(train_idx) - pos), 1.0)
        pos_weight = min(neg / pos, 15.0)

        best, best_params, patience = -np.inf, None, 0
        for epoch in range(c.epochs):
            batch = rng.permutation(train_idx)[: c.batch_size]
            cache = self._forward(Xs[batch], M[batch], training=True)
            prob = _sigmoid(cache["logits"])
            w_b = np.where(y[batch] > 0.5, pos_weight, 1.0)
            dlogits = w_b * (prob - y[batch]) / batch.size
            grads = self._backward(cache, dlogits)
            # BPTT on 48 steps can spike; clip the global norm.
            _clip(grads, 5.0)
            opt.step(self.params, grads)

            if val_idx is not None and (epoch % 2 == 0 or epoch == c.epochs - 1):
                pv = _sigmoid(self._forward(Xs[val_idx], M[val_idx])["logits"])
                score = _auc(y[val_idx], pv)
                self.history.append({"epoch": epoch, "val_auc": float(score)})
                if score > best + 1e-5:
                    best, patience = score, 0
                    best_params = {k: v.copy() for k, v in self.params.items()}
                else:
                    patience += 1
                    if patience >= 10:
                        break
                if verbose and epoch % 10 == 0:
                    print(f"  tgn epoch {epoch:3d}  val_auc={score:.4f}")
        if best_params is not None:
            self.params = best_params
        return self

    def _standardise(self, X: np.ndarray, M: np.ndarray) -> np.ndarray:
        Xs = (X - self.mu) / self.sigma
        return np.clip(Xs, -8, 8) * M[..., None]

    def predict_proba(self, X: np.ndarray, M: np.ndarray,
                      chunk: int = 2_048) -> np.ndarray:
        Xs = self._standardise(np.asarray(X, dtype=np.float64), M)
        out = np.zeros(X.shape[0])
        for i in range(0, X.shape[0], chunk):
            sl = slice(i, i + chunk)
            out[sl] = _sigmoid(self._forward(Xs[sl], M[sl])["logits"])
        return out

    def event_saliency(self, X: np.ndarray, M: np.ndarray) -> np.ndarray:
        """Per-event gradient magnitude - which moments drove the score."""
        Xs = self._standardise(np.asarray(X, dtype=np.float64), M)
        cache = self._forward(Xs, M)
        # Backward pass run purely to obtain dLogit/dX, not to update anything.
        grads_x = self._backward_input(cache, np.ones(Xs.shape[0]))
        return np.abs(grads_x[:, :, :N_EVENT_FEATURES]).sum(axis=2) * M

    def _backward_input(self, cache: dict, dlogits: np.ndarray) -> np.ndarray:
        p = self.params
        Xf, M = cache["Xf"], cache["M"]
        _, T, _ = Xf.shape
        o = cache["o"]
        do = np.outer(dlogits, p["w"])
        dpre_o = do * (1.0 - o ** 2)
        dh = dpre_o @ p["Wo"].T
        dXf = np.zeros_like(Xf)
        for t in range(T - 1, -1, -1):
            z, r, nt = cache["z"][t], cache["r"][t], cache["nt"][t]
            g, h_prev = cache["g"][t], cache["h_prev"][t]
            m = M[:, t][:, None]
            dh_eff = dh * m
            dh_prev = dh * (1.0 - m)
            dz = dh_eff * (h_prev - nt)
            dnt = dh_eff * (1.0 - z)
            dh_prev = dh_prev + dh_eff * z
            dxn = dnt * (1.0 - nt ** 2)
            dr, dg = dxn * g, dxn * r
            dh_prev = dh_prev + dg @ p["Un"].T
            dxr = dr * r * (1.0 - r)
            dh_prev = dh_prev + dxr @ p["Ur"].T
            dxz = dz * z * (1.0 - z)
            dh_prev = dh_prev + dxz @ p["Uz"].T
            dXf[:, t, :] = dxz @ p["Wz"].T + dxr @ p["Wr"].T + dxn @ p["Wn"].T
            dh = dh_prev
        return dXf

    # -- persistence --------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path.with_suffix(".npz"), mu=self.mu, sigma=self.sigma,
                 hidden_dim=np.array([self.config.hidden_dim]),
                 time_dim=np.array([self.config.time_dim]),
                 max_events=np.array([self.config.max_events]),
                 **self.params)

    @classmethod
    def load(cls, path: Path) -> "TemporalGraphNetwork":
        data = np.load(Path(path).with_suffix(".npz"))
        cfg = TemporalConfig(
            hidden_dim=int(data["hidden_dim"][0]),
            time_dim=int(data["time_dim"][0]),
            max_events=int(data["max_events"][0]),
        )
        obj = cls(cfg)
        obj.mu, obj.sigma = data["mu"], data["sigma"]
        obj.params = {k: data[k] for k in data.files
                      if k not in ("mu", "sigma", "hidden_dim", "time_dim", "max_events")}
        return obj


def _clip(grads: dict[str, np.ndarray], max_norm: float) -> None:
    total = np.sqrt(sum(float((g ** 2).sum()) for g in grads.values()))
    if total > max_norm:
        scale = max_norm / (total + 1e-12)
        for k in grads:
            grads[k] *= scale


__all__ = ["TemporalGraphNetwork", "build_sequences", "EVENT_FEATURES",
           "N_EVENT_FEATURES"]
