"""Layer 2 - turn a raw event stream into behavioural feature vectors.

Design rules this module follows:

1. **No label leakage.** Nothing here reads ``is_fraud`` or ``is_mule``.
2. **Intelligence is kept separate.** NCRP tickets literally name the
   beneficiary account, so folding them into the supervised features would let
   the model memorise the answer key. Ticket/IoC signals live in
   :func:`build_intel_features` and are fused only at Layer 7. That separation
   is what lets us report "N% of the mules we caught were never reported".
3. **As-of correctness.** Every builder takes ``as_of``; nothing after that
   instant is visible. The same code therefore serves both the nightly batch
   and the real-time path.
4. **Vectorised.** Pass-through matching and burst counting are the only
   genuinely awkward operations; both are done with a sorted composite key and
   ``searchsorted`` rather than per-account Python loops.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bodhi.config import CASHOUT_CHANNELS, FEATURES, REGULATORY
from bodhi.data.generator import CASH_POOL

#: Composite-key multiplier: strictly greater than any epoch second we handle.
_KEY = 2_000_000_000

#: Features fed to the supervised models. Order is part of the model contract.
BEHAVIOUR_FEATURES: tuple[str, ...] = (
    # volume & value
    "in_count", "out_count", "in_amount", "out_amount", "total_count",
    "log_in_amount", "log_out_amount",
    "avg_in_amount", "avg_out_amount", "max_in_amount", "max_out_amount",
    "median_in_amount", "amount_cv_in", "amount_cv_out",
    # flow shape
    "passthrough_ratio", "retention_ratio", "cashout_ratio",
    "rapid_passthrough_ratio", "median_in_to_out_min", "min_in_to_out_min",
    # counterparty structure
    "unique_payers", "unique_payees", "fan_in_ratio", "fan_out_ratio",
    "payer_concentration", "payee_concentration", "reciprocity",
    "unique_payer_per_day", "in_out_degree_ratio",
    # timing
    "active_days", "txn_per_active_day", "burst_max_30min",
    "max_amount_1h", "night_frac", "weekend_frac", "hour_entropy",
    "max_gap_days", "silence_before_first_days", "dormancy_burst_score",
    "recency_days",
    # structuring / evasion
    "structuring_frac", "round_amount_frac", "sub_threshold_frac",
    "new_beneficiary_frac", "first_time_payer_frac",
    # device & network hygiene
    "n_devices", "device_max_accounts", "ip_max_accounts",
    "device_entropy", "shared_device_flag",
    # channel mix
    "frac_upi", "frac_imps", "frac_neft_rtgs", "frac_atm_aeps", "frac_card",
    # static / KYC
    "account_vintage_days", "kyc_min_flag", "kyc_video_flag", "customer_age",
    "is_merchant_flag",
)

INTEL_FEATURES: tuple[str, ...] = (
    "ncrp_ticket_count", "ncrp_amount", "ncrp_recency_days", "ncrp_distinct_victims",
    "ioc_hit", "ioc_max_confidence", "ioc_is_preemptive",
    "regulatory_listed",
)

#: Columns that constitute *positive* evidence. ``ncrp_recency_days`` is
#: deliberately excluded: it is a sentinel (999) for accounts nobody has ever
#: reported, so a plain row-sum would declare that every account in the bank
#: carries intelligence.
INTEL_EVIDENCE_COLUMNS: tuple[str, ...] = (
    "ncrp_ticket_count", "ioc_hit", "regulatory_listed",
)


def has_intelligence(intel: pd.DataFrame) -> np.ndarray:
    """Boolean mask: does this account have any external intelligence at all?"""
    cols = [c for c in INTEL_EVIDENCE_COLUMNS if c in intel.columns]
    if not cols:
        return np.zeros(len(intel), dtype=bool)
    return (intel[cols].to_numpy(dtype=float) > 0).any(axis=1)


# --------------------------------------------------------------------------
# low-level vectorised primitives
# --------------------------------------------------------------------------


def _codes(values: np.ndarray, index: pd.Index) -> np.ndarray:
    """Map account ids onto contiguous integer codes (-1 when unknown)."""
    return index.get_indexer(values)


def _window_counts(codes: np.ndarray, ts: np.ndarray, window_s: float,
                   weights: np.ndarray | None = None) -> np.ndarray:
    """For every event, how many (or how much) happened in the next window.

    Both arrays must already be sorted by ``(code, ts)``. The trick is a single
    ``int64`` composite key ``code * _KEY + ts`` which makes one global
    ``searchsorted`` equivalent to a per-account one.
    """
    key = codes.astype(np.int64) * _KEY + ts.astype(np.int64)
    hi = codes.astype(np.int64) * _KEY + (ts + window_s).astype(np.int64)
    i0 = np.arange(codes.size)
    i1 = np.searchsorted(key, hi, side="right")
    if weights is None:
        return (i1 - i0).astype(np.float64)
    cum = np.concatenate([[0.0], np.cumsum(weights)])
    return cum[i1] - cum[i0]


def _forward_window_sum(
    q_codes: np.ndarray, q_ts: np.ndarray,
    e_codes: np.ndarray, e_ts: np.ndarray, e_amt: np.ndarray,
    window_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Total ``e_amt`` occurring in ``(q_ts, q_ts + window]`` for the same code.

    ``e_*`` must be sorted by ``(code, ts)``. Returns the summed amount and the
    delay to the first matching event (``inf`` when there is none).
    """
    if e_codes.size == 0:
        return np.zeros(q_codes.size), np.full(q_codes.size, np.inf)
    ekey = e_codes.astype(np.int64) * _KEY + e_ts.astype(np.int64)
    lo = q_codes.astype(np.int64) * _KEY + q_ts.astype(np.int64)
    hi = q_codes.astype(np.int64) * _KEY + (q_ts + window_s).astype(np.int64)
    i0 = np.searchsorted(ekey, lo, side="right")
    i1 = np.searchsorted(ekey, hi, side="right")
    cum = np.concatenate([[0.0], np.cumsum(e_amt)])
    total = cum[i1] - cum[i0]
    idx = np.minimum(i0, e_codes.size - 1)
    same = (i0 < e_codes.size) & (e_codes[idx] == q_codes)
    delay = np.where(same, e_ts[idx] - q_ts, np.inf)
    return total, delay


def _grouped(codes: np.ndarray, values: np.ndarray, n: int, how: str = "sum") -> np.ndarray:
    """Fast group-by over contiguous integer codes."""
    out = np.zeros(n, dtype=np.float64)
    if codes.size == 0:
        return out
    if how == "sum":
        np.add.at(out, codes, values)
    elif how == "max":
        out[:] = -np.inf
        np.maximum.at(out, codes, values)
        out[np.isinf(out)] = 0.0
    elif how == "min":
        out[:] = np.inf
        np.minimum.at(out, codes, values)
        out[np.isinf(out)] = 0.0
    else:  # pragma: no cover - guarded by callers
        raise ValueError(how)
    return out


def _grouped_median(codes: np.ndarray, values: np.ndarray, n: int) -> np.ndarray:
    """Per-code median via a single sort (values must be finite)."""
    out = np.zeros(n, dtype=np.float64)
    if codes.size == 0:
        return out
    order = np.lexsort((values, codes))
    c, v = codes[order], values[order]
    starts = np.searchsorted(c, np.arange(n), side="left")
    ends = np.searchsorted(c, np.arange(n), side="right")
    sizes = ends - starts
    ok = sizes > 0
    mid = starts[ok] + (sizes[ok] - 1) // 2
    mid2 = starts[ok] + sizes[ok] // 2
    out[ok] = 0.5 * (v[mid] + v[mid2])
    return out


def _nunique(codes: np.ndarray, other: np.ndarray, n: int) -> np.ndarray:
    """Distinct ``other`` per ``code``."""
    out = np.zeros(n, dtype=np.float64)
    if codes.size == 0:
        return out
    pairs = np.unique(np.stack([codes, other], axis=1), axis=0)
    uniq, cnt = np.unique(pairs[:, 0], return_counts=True)
    out[uniq] = cnt
    return out


def _entropy(counts: np.ndarray, axis: int = 1) -> np.ndarray:
    tot = counts.sum(axis=axis, keepdims=True)
    p = np.divide(counts, tot, out=np.zeros_like(counts, dtype=float), where=tot > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        h = -np.where(p > 0, p * np.log(p), 0.0).sum(axis=axis)
    return h


# --------------------------------------------------------------------------
# main builder
# --------------------------------------------------------------------------


def build_account_features(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame,
    as_of: float | None = None,
) -> pd.DataFrame:
    """Compute the behavioural feature matrix, one row per account.

    Parameters
    ----------
    transactions
        Normalised events with at least ``ts, src_account, dst_account,
        amount, channel, txn_type``.
    accounts
        Static customer attributes.
    as_of
        Epoch seconds. Events strictly after this instant are ignored.
    """
    acct_index = pd.Index(accounts["account_id"].to_numpy())
    n = len(acct_index)

    tx = transactions
    if as_of is not None:
        tx = tx[tx["ts"] <= as_of]
    if len(tx) == 0:
        empty = pd.DataFrame(0.0, index=acct_index, columns=list(BEHAVIOUR_FEATURES))
        empty.index.name = "account_id"
        return empty
    as_of = float(as_of if as_of is not None else tx["ts"].max())

    ts = tx["ts"].to_numpy(dtype=np.float64)
    amt = tx["amount"].to_numpy(dtype=np.float64)
    src = _codes(tx["src_account"].to_numpy(), acct_index)
    dst = _codes(tx["dst_account"].to_numpy(), acct_index)
    channel = tx["channel"].to_numpy()
    is_cash = np.isin(channel, list(CASHOUT_CHANNELS))

    out_mask = src >= 0
    in_mask = dst >= 0

    # ---- debit side -----------------------------------------------------
    o_c, o_ts, o_amt = src[out_mask], ts[out_mask], amt[out_mask]
    o_chan, o_cash = channel[out_mask], is_cash[out_mask]
    o_dst = dst[out_mask]
    o_ben = tx["beneficiary_age_hours"].to_numpy(dtype=np.float64)[out_mask] \
        if "beneficiary_age_hours" in tx.columns else np.full(o_c.size, np.nan)

    out_count = _grouped(o_c, np.ones_like(o_amt), n)
    out_amount = _grouped(o_c, o_amt, n)
    max_out = _grouped(o_c, o_amt, n, "max")
    sq_out = _grouped(o_c, o_amt ** 2, n)

    cash_amount = _grouped(o_c[o_cash], o_amt[o_cash], n)

    # ---- credit side ----------------------------------------------------
    i_c, i_ts, i_amt = dst[in_mask], ts[in_mask], amt[in_mask]
    i_src = src[in_mask]

    in_count = _grouped(i_c, np.ones_like(i_amt), n)
    in_amount = _grouped(i_c, i_amt, n)
    max_in = _grouped(i_c, i_amt, n, "max")
    sq_in = _grouped(i_c, i_amt ** 2, n)
    median_in = _grouped_median(i_c, i_amt, n)

    # ---- pass-through: does money rest, or does it bounce straight out? --
    d_order = np.lexsort((o_ts, o_c))
    dc, dt_, dm = o_c[d_order], o_ts[d_order], o_amt[d_order]
    rapid_out, delay = _forward_window_sum(
        i_c, i_ts, dc, dt_, dm, FEATURES.rapid_passthrough_min * 60.0
    )
    matched = np.minimum(rapid_out, i_amt)
    rapid_amount = _grouped(i_c, matched, n)
    finite = np.isfinite(delay)
    median_delay = _grouped_median(i_c[finite], delay[finite] / 60.0, n)
    min_delay = _grouped(i_c[finite], delay[finite] / 60.0, n, "min")

    # ---- counterparty structure -----------------------------------------
    unique_payers = _nunique(i_c, i_src, n)
    unique_payees = _nunique(o_c[o_dst >= 0], o_dst[o_dst >= 0], n)

    pair_in = _pair_sums(i_c, i_src, i_amt, n)
    pair_out = _pair_sums(o_c[o_dst >= 0], o_dst[o_dst >= 0], o_amt[o_dst >= 0], n)
    payer_conc = np.divide(pair_in, np.maximum(in_amount, 1.0))
    payee_conc = np.divide(pair_out, np.maximum(out_amount, 1.0))

    reciprocity = _reciprocity(i_c, i_src, o_c[o_dst >= 0], o_dst[o_dst >= 0], n)

    # ---- timing ---------------------------------------------------------
    all_c = np.concatenate([o_c, i_c])
    all_ts = np.concatenate([o_ts, i_ts])
    order = np.lexsort((all_ts, all_c))
    ac, at = all_c[order], all_ts[order]

    burst = _window_counts(ac, at, FEATURES.burst_window_min * 60.0)
    burst_max = _grouped(ac, burst, n, "max")

    all_amt = np.concatenate([o_amt, i_amt])[order]
    amt_1h = _window_counts(ac, at, 3_600.0, weights=all_amt)
    max_amt_1h = _grouped(ac, amt_1h, n, "max")

    first_ts = _grouped(ac, at, n, "min")
    last_ts = _grouped(ac, at, n, "max")
    total_count = _grouped(ac, np.ones_like(at), n)

    day_idx = np.floor(at / 86_400.0)
    active_days = _nunique(ac, day_idx.astype(np.int64), n)

    gaps = np.diff(at)
    same = ac[1:] == ac[:-1]
    max_gap = _grouped(ac[1:][same], gaps[same], n, "max") / 86_400.0

    hours = ((at % 86_400) / 3_600).astype(int)
    lo_h, hi_h = FEATURES.night_hours
    night = ((hours >= lo_h) & (hours <= hi_h)).astype(float)
    night_frac = np.divide(_grouped(ac, night, n), np.maximum(total_count, 1.0))

    dow = (np.floor(at / 86_400).astype(int) + 4) % 7  # epoch day 0 == Thursday
    weekend = np.isin(dow, (5, 6)).astype(float)
    weekend_frac = np.divide(_grouped(ac, weekend, n), np.maximum(total_count, 1.0))

    hour_hist = np.zeros((n, 24))
    np.add.at(hour_hist, (ac, hours), 1.0)
    hour_entropy = _entropy(hour_hist)

    window_start = float(ts.min())
    silence_before = np.where(total_count > 0, (first_ts - window_start) / 86_400.0, 0.0)
    recency = np.where(total_count > 0, (as_of - last_ts) / 86_400.0, 999.0)
    span_days = np.maximum((last_ts - first_ts) / 86_400.0, 1e-9)
    txn_per_active_day = np.divide(total_count, np.maximum(active_days, 1.0))
    # Long silence followed by dense activity: the rented-account signature.
    dormancy_burst = np.where(
        total_count > 0,
        silence_before / np.maximum(span_days + silence_before, 1.0) *
        np.minimum(txn_per_active_day / 5.0, 3.0),
        0.0,
    )

    # ---- structuring / evasion ------------------------------------------
    lo_b, hi_b = REGULATORY.structuring_band
    structuring = ((o_amt >= lo_b) & (o_amt < hi_b)).astype(float)
    structuring_frac = np.divide(_grouped(o_c, structuring, n), np.maximum(out_count, 1.0))
    round_amt = (np.mod(o_amt, 1_000.0) == 0).astype(float)
    round_frac = np.divide(_grouped(o_c, round_amt, n), np.maximum(out_count, 1.0))
    sub_thr = (o_amt < REGULATORY.upi_p2p_cap).astype(float) * (o_amt > 20_000).astype(float)
    sub_thr_frac = np.divide(_grouped(o_c, sub_thr, n), np.maximum(out_count, 1.0))

    fresh = np.nan_to_num(o_ben, nan=9_999.0) < REGULATORY.fresh_beneficiary_hours
    new_ben_frac = np.divide(_grouped(o_c, fresh.astype(float), n),
                             np.maximum(out_count, 1.0))
    i_ben = tx["beneficiary_age_hours"].to_numpy(dtype=np.float64)[in_mask] \
        if "beneficiary_age_hours" in tx.columns else np.full(i_c.size, np.nan)
    fresh_in = np.nan_to_num(i_ben, nan=9_999.0) < REGULATORY.fresh_beneficiary_hours
    first_time_payer_frac = np.divide(_grouped(i_c, fresh_in.astype(float), n),
                                      np.maximum(in_count, 1.0))

    # ---- devices & IPs ---------------------------------------------------
    n_devices, device_max_accounts, device_entropy = _device_stats(
        tx, acct_index, n, "device_id")
    _, ip_max_accounts, _ = _device_stats(tx, acct_index, n, "ip_address")

    # ---- channel mix -----------------------------------------------------
    chan_all = np.concatenate([o_chan, channel[in_mask]])[order]
    def _cfrac(names: tuple[str, ...]) -> np.ndarray:
        m = np.isin(chan_all, list(names)).astype(float)
        return np.divide(_grouped(ac, m, n), np.maximum(total_count, 1.0))

    # ---- static attributes ----------------------------------------------
    vintage = accounts["account_vintage_days"].to_numpy(dtype=np.float64) \
        if "account_vintage_days" in accounts.columns else np.full(n, 1_000.0)
    kyc = accounts["kyc_level"].to_numpy() if "kyc_level" in accounts.columns \
        else np.full(n, "FULL")
    age = accounts["customer_age"].to_numpy(dtype=np.float64) \
        if "customer_age" in accounts.columns else np.full(n, 35.0)
    merch = accounts["is_merchant"].to_numpy().astype(float) \
        if "is_merchant" in accounts.columns else np.zeros(n)

    def _cv(sq: np.ndarray, s: np.ndarray, c: np.ndarray) -> np.ndarray:
        mean = np.divide(s, np.maximum(c, 1.0))
        var = np.maximum(np.divide(sq, np.maximum(c, 1.0)) - mean ** 2, 0.0)
        return np.divide(np.sqrt(var), np.maximum(mean, 1.0))

    feats = {
        "in_count": in_count,
        "out_count": out_count,
        "in_amount": in_amount,
        "out_amount": out_amount,
        "total_count": total_count,
        "log_in_amount": np.log1p(in_amount),
        "log_out_amount": np.log1p(out_amount),
        "avg_in_amount": np.divide(in_amount, np.maximum(in_count, 1.0)),
        "avg_out_amount": np.divide(out_amount, np.maximum(out_count, 1.0)),
        "max_in_amount": max_in,
        "max_out_amount": max_out,
        "median_in_amount": median_in,
        "amount_cv_in": _cv(sq_in, in_amount, in_count),
        "amount_cv_out": _cv(sq_out, out_amount, out_count),
        "passthrough_ratio": np.divide(out_amount, np.maximum(in_amount, 1.0)),
        "retention_ratio": np.clip(1.0 - np.divide(out_amount, np.maximum(in_amount, 1.0)),
                                   -5.0, 1.0),
        "cashout_ratio": np.divide(cash_amount, np.maximum(in_amount, 1.0)),
        "rapid_passthrough_ratio": np.divide(rapid_amount, np.maximum(in_amount, 1.0)),
        "median_in_to_out_min": np.minimum(median_delay, 10_000.0),
        "min_in_to_out_min": np.minimum(min_delay, 10_000.0),
        "unique_payers": unique_payers,
        "unique_payees": unique_payees,
        "fan_in_ratio": np.divide(unique_payers, np.maximum(in_count, 1.0)),
        "fan_out_ratio": np.divide(unique_payees, np.maximum(out_count, 1.0)),
        "payer_concentration": payer_conc,
        "payee_concentration": payee_conc,
        "reciprocity": reciprocity,
        "unique_payer_per_day": np.divide(unique_payers, np.maximum(active_days, 1.0)),
        "in_out_degree_ratio": np.divide(unique_payers, np.maximum(unique_payees, 1.0)),
        "active_days": active_days,
        "txn_per_active_day": txn_per_active_day,
        "burst_max_30min": burst_max,
        "max_amount_1h": max_amt_1h,
        "night_frac": night_frac,
        "weekend_frac": weekend_frac,
        "hour_entropy": hour_entropy,
        "max_gap_days": max_gap,
        "silence_before_first_days": silence_before,
        "dormancy_burst_score": dormancy_burst,
        "recency_days": recency,
        "structuring_frac": structuring_frac,
        "round_amount_frac": round_frac,
        "sub_threshold_frac": sub_thr_frac,
        "new_beneficiary_frac": new_ben_frac,
        "first_time_payer_frac": first_time_payer_frac,
        "n_devices": n_devices,
        "device_max_accounts": device_max_accounts,
        "ip_max_accounts": ip_max_accounts,
        "device_entropy": device_entropy,
        "shared_device_flag": (device_max_accounts > 3).astype(float),
        "frac_upi": _cfrac(("UPI",)),
        "frac_imps": _cfrac(("IMPS",)),
        "frac_neft_rtgs": _cfrac(("NEFT", "RTGS")),
        "frac_atm_aeps": _cfrac(("ATM", "AEPS")),
        "frac_card": _cfrac(("CARD", "WALLET")),
        "account_vintage_days": vintage,
        "kyc_min_flag": (kyc == "MIN").astype(float),
        "kyc_video_flag": (kyc == "VIDEO").astype(float),
        "customer_age": age,
        "is_merchant_flag": merch,
    }

    df = pd.DataFrame(feats, index=acct_index)
    df.index.name = "account_id"
    df = df[list(BEHAVIOUR_FEATURES)]
    return df.replace([np.inf, -np.inf], 0.0).fillna(0.0)


def _pair_sums(codes: np.ndarray, other: np.ndarray, values: np.ndarray,
               n: int) -> np.ndarray:
    """Largest amount contributed by any single counterparty (concentration)."""
    out = np.zeros(n, dtype=np.float64)
    if codes.size == 0:
        return out
    key = codes.astype(np.int64) * _KEY + np.maximum(other, 0).astype(np.int64)
    order = np.argsort(key, kind="stable")
    k, v = key[order], values[order]
    bounds = np.flatnonzero(np.concatenate([[True], k[1:] != k[:-1], [True]]))
    sums = np.add.reduceat(v, bounds[:-1])
    grp = (k[bounds[:-1]] // _KEY).astype(np.int64)
    np.maximum.at(out, grp, sums)
    return out


def _reciprocity(i_c, i_src, o_c, o_dst, n: int) -> np.ndarray:
    """Share of an account's payees who also pay it back.

    Two-way money movement is the norm between friends and family; a
    collection mule's edges are almost perfectly one-directional.
    """
    out = np.zeros(n, dtype=np.float64)
    if i_c.size == 0 or o_c.size == 0:
        return out
    incoming = np.unique(i_c.astype(np.int64) * _KEY + i_src.astype(np.int64))
    outgoing = o_c.astype(np.int64) * _KEY + o_dst.astype(np.int64)
    reciprocal = np.isin(outgoing, incoming, assume_unique=False).astype(float)
    tot = _grouped(o_c, np.ones_like(reciprocal), n)
    rec = _grouped(o_c, reciprocal, n)
    return np.divide(rec, np.maximum(tot, 1.0))


def _device_stats(tx: pd.DataFrame, acct_index: pd.Index, n: int,
                  col: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-account device (or IP) diversity and the worst sharing factor."""
    zeros = np.zeros(n)
    if col not in tx.columns:
        return zeros, zeros, zeros
    sub = tx.loc[tx[col].notna(), ["src_account", col]]
    if len(sub) == 0:
        return zeros, zeros, zeros
    codes = acct_index.get_indexer(sub["src_account"].to_numpy())
    keep = codes >= 0
    codes = codes[keep]
    if codes.size == 0:
        return zeros, zeros, zeros
    dev = pd.factorize(sub[col].to_numpy()[keep])[0]

    pairs = np.unique(np.stack([codes, dev], axis=1), axis=0)
    n_dev = np.zeros(n)
    u, c = np.unique(pairs[:, 0], return_counts=True)
    n_dev[u] = c
    # How many distinct accounts sit behind each device?
    du, dc = np.unique(pairs[:, 1], return_counts=True)
    dev_load = np.zeros(dev.max() + 1)
    dev_load[du] = dc
    max_accounts = np.zeros(n)
    np.maximum.at(max_accounts, pairs[:, 0], dev_load[pairs[:, 1]])

    counts = np.zeros((n, int(dev.max()) + 1)) if dev.max() < 400 else None
    if counts is not None:
        np.add.at(counts, (codes, dev), 1.0)
        ent = _entropy(counts)
    else:
        # Too many devices for a dense histogram: entropy of the per-account
        # device usage counts computed sparsely.
        key = codes.astype(np.int64) * _KEY + dev.astype(np.int64)
        order = np.argsort(key, kind="stable")
        k = key[order]
        bounds = np.flatnonzero(np.concatenate([[True], k[1:] != k[:-1], [True]]))
        cnt = np.diff(bounds).astype(float)
        grp = (k[bounds[:-1]] // _KEY).astype(np.int64)
        tot = np.zeros(n)
        np.add.at(tot, grp, cnt)
        p = cnt / np.maximum(tot[grp], 1.0)
        ent = np.zeros(n)
        np.add.at(ent, grp, -p * np.log(np.maximum(p, 1e-12)))
    return n_dev, max_accounts, ent


# --------------------------------------------------------------------------
# intelligence features (kept out of the supervised models on purpose)
# --------------------------------------------------------------------------


def build_intel_features(
    accounts: pd.DataFrame,
    fraud_alerts: pd.DataFrame | None = None,
    iocs: pd.DataFrame | None = None,
    regulatory: pd.DataFrame | None = None,
    as_of: float | None = None,
) -> pd.DataFrame:
    """External-intelligence signals: NCRP tickets, APK IoCs, RBI listings."""
    acct_index = pd.Index(accounts["account_id"].to_numpy())
    n = len(acct_index)
    df = pd.DataFrame(0.0, index=acct_index, columns=list(INTEL_FEATURES))
    df.index.name = "account_id"

    if fraud_alerts is not None and len(fraud_alerts):
        fa = fraud_alerts.copy()
        rep = pd.to_datetime(fa["reported_at"], utc=True).astype("int64") / 1e9
        if as_of is not None:
            fa = fa[rep.to_numpy() <= as_of]
            rep = rep[rep.to_numpy() <= as_of]
        if len(fa):
            codes = acct_index.get_indexer(fa["beneficiary_account"].to_numpy())
            keep = codes >= 0
            codes, repv = codes[keep], rep.to_numpy()[keep]
            amts = fa["amount"].to_numpy(dtype=float)[keep]
            cnt = np.zeros(n); np.add.at(cnt, codes, 1.0)
            amount = np.zeros(n); np.add.at(amount, codes, amts)
            last = np.full(n, -np.inf); np.maximum.at(last, codes, repv)
            ref = as_of if as_of is not None else float(repv.max())
            recency = np.where(np.isfinite(last), (ref - last) / 86_400.0, 999.0)
            victims = _nunique(codes,
                               pd.factorize(fa["victim_account"].to_numpy()[keep])[0], n)
            df["ncrp_ticket_count"] = cnt
            df["ncrp_amount"] = amount
            df["ncrp_recency_days"] = np.clip(recency, 0, 999)
            df["ncrp_distinct_victims"] = victims

    if iocs is not None and len(iocs):
        io = iocs.copy()
        obs = pd.to_datetime(io["observed_at"], utc=True).astype("int64") / 1e9
        if as_of is not None:
            mask = obs.to_numpy() <= as_of
            io, obs = io[mask], obs[mask]
        if len(io):
            by_account = io[io["ioc_type"] == "ACCOUNT"]["value"].to_numpy()
            by_vpa = io[io["ioc_type"] == "UPI_VPA"]["value"].to_numpy()
            conf = io["confidence"].to_numpy(dtype=float)
            hit = np.zeros(n)
            best = np.zeros(n)
            c1 = acct_index.get_indexer(by_account)
            hit[c1[c1 >= 0]] = 1.0
            vpa_map = pd.Index(accounts["vpa"].to_numpy()) if "vpa" in accounts.columns \
                else pd.Index([])
            if len(vpa_map):
                c2 = vpa_map.get_indexer(by_vpa)
                hit[c2[c2 >= 0]] = 1.0
            codes_all = np.concatenate([
                c1[c1 >= 0],
                (vpa_map.get_indexer(by_vpa) if len(vpa_map) else np.array([], dtype=int)),
            ])
            codes_all = codes_all[codes_all >= 0]
            if codes_all.size:
                conf_all = np.resize(conf, codes_all.size)
                np.maximum.at(best, codes_all, conf_all)
            df["ioc_hit"] = hit
            df["ioc_max_confidence"] = best
            # Pre-emptive = the malware was analysed before any money moved.
            df["ioc_is_preemptive"] = (hit > 0).astype(float)

    if regulatory is not None and len(regulatory):
        rg = regulatory.copy()
        pub = pd.to_datetime(rg["published_at"], utc=True).astype("int64") / 1e9
        if as_of is not None:
            rg = rg[pub.to_numpy() <= as_of]
        listed: list[str] = []
        for _, row in rg.iterrows():
            if row.get("directive_type") == "MULE_ACCOUNT_LIST":
                ents = row.get("entities") or ""
                listed.extend([e for e in str(ents).split(",") if e])
        if listed:
            c = acct_index.get_indexer(np.array(listed))
            arr = np.zeros(n)
            arr[c[c >= 0]] = 1.0
            df["regulatory_listed"] = arr

    return df


# --------------------------------------------------------------------------
# per-transaction features for the real-time path
# --------------------------------------------------------------------------


def build_transaction_features(
    tx: pd.DataFrame, account_features: pd.DataFrame
) -> pd.DataFrame:
    """Join an in-flight transaction with both parties' behavioural profiles."""
    af = account_features
    src = af.reindex(tx["src_account"].to_numpy()).fillna(0.0)
    dst = af.reindex(tx["dst_account"].to_numpy()).fillna(0.0)
    out = pd.DataFrame(index=tx.index)
    out["amount"] = tx["amount"].to_numpy(dtype=float)
    out["log_amount"] = np.log1p(out["amount"])
    out["hour"] = pd.to_datetime(tx["ts"], unit="s", utc=True).dt.hour.to_numpy()
    out["is_night"] = ((out["hour"] >= FEATURES.night_hours[0]) &
                       (out["hour"] <= FEATURES.night_hours[1])).astype(float)
    ben = tx.get("beneficiary_age_hours")
    out["beneficiary_age_hours"] = (
        np.nan_to_num(np.asarray(ben, dtype=float), nan=9_999.0)
        if ben is not None else 9_999.0
    )
    out["fresh_beneficiary"] = (out["beneficiary_age_hours"] <
                                REGULATORY.fresh_beneficiary_hours).astype(float)
    out["is_cashout"] = np.isin(tx["channel"].to_numpy(), list(CASHOUT_CHANNELS)).astype(float)
    out["to_cash_pool"] = (tx["dst_account"].to_numpy() == CASH_POOL).astype(float)
    for name, frame in (("src", src), ("dst", dst)):
        for col in ("passthrough_ratio", "rapid_passthrough_ratio", "cashout_ratio",
                    "unique_payers", "burst_max_30min", "device_max_accounts",
                    "account_vintage_days", "kyc_min_flag", "structuring_frac",
                    "dormancy_burst_score", "night_frac"):
            out[f"{name}_{col}"] = frame[col].to_numpy()
    out["amount_vs_src_avg"] = out["amount"] / np.maximum(src["avg_out_amount"].to_numpy(), 1.0)
    out["amount_vs_dst_avg"] = out["amount"] / np.maximum(dst["avg_in_amount"].to_numpy(), 1.0)
    return out.replace([np.inf, -np.inf], 0.0).fillna(0.0)


__all__ = [
    "BEHAVIOUR_FEATURES",
    "INTEL_FEATURES",
    "INTEL_EVIDENCE_COLUMNS",
    "has_intelligence",
    "build_account_features",
    "build_intel_features",
    "build_transaction_features",
]
