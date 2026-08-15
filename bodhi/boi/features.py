"""Feature engineering on top of the bank's 3,900 supplied columns.

The organisers have already done the hard aggregation work, so adding a few
hundred more raw ratios would be noise. What the supplied set does *not*
contain is information that only exists **across** its own columns:

``family roll-ups``
    The same measurement appears at 7, 14 and 31 days. Summarising a family
    across its windows gives the model a stable, low-variance version of a
    signal that is individually noisy, and a slope that says which direction
    the account is moving in.

``channel concentration``
    Whether activity is spread across many channels or funnelled through one.
    A mule funnels: credits in through UPI, out through cash. No single
    supplied column expresses that, because each is scoped to one channel.

``missingness structure``
    In a bank extract, *which* blocks are null is itself informative - an
    account with no cheque history at all is a different animal from one with
    a quiet cheque history. Counting nulls per family costs nothing and is
    frequently among the strongest engineered features.

``customer- versus bank-induced balance``
    Fees and GST postings are bank-induced. An account whose activity is almost
    entirely bank-induced is dormant in the way that matters, regardless of how
    many postings it has.

Every derived column is prefixed ``BX_`` so it can never be confused with a
supplied one.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from bodhi.boi.schema import parse_feature

PREFIX = "BX_"

#: Families smaller than this are not worth rolling up.
MIN_FAMILY_SIZE = 2


def _safe_ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.divide(a, b, out=np.zeros_like(a, dtype=float),
                     where=np.abs(b) > 1e-9)


def build(X: pd.DataFrame, *, max_families: int = 400) -> pd.DataFrame:
    """Return ``X`` with engineered columns appended.

    ``max_families`` caps the roll-up work: the dictionary has over a thousand
    families and rolling up all of them would double the width of an already
    very wide matrix for diminishing return. Families are ranked by size, which
    favours the blocks measured across the most windows.
    """
    out = X.copy()
    numeric = X.select_dtypes(include=[np.number])
    if numeric.empty:
        return out

    parsed = {c: parse_feature(c) for c in numeric.columns}

    # ---- family roll-ups across observation windows ----------------------
    families: dict[str, list[str]] = {}
    for col, p in parsed.items():
        if p.is_static or not p.window:
            continue
        families.setdefault(p.family, []).append(col)

    ranked = sorted((f for f in families.items() if len(f[1]) >= MIN_FAMILY_SIZE),
                    key=lambda kv: -len(kv[1]))[:max_families]

    new: dict[str, np.ndarray] = {}
    for family, cols in ranked:
        block = numeric[cols].to_numpy(dtype=float)
        # A row can be entirely missing for a family - an account with no cheque
        # history at all. numpy warns and returns NaN for that, which is the
        # right value; the warning is noise, so it is silenced deliberately
        # rather than by luck.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            new[f"{PREFIX}{family}__mean"] = np.nanmean(block, axis=1)
            new[f"{PREFIX}{family}__max"] = np.nanmax(block, axis=1)
            new[f"{PREFIX}{family}__std"] = np.nanstd(block, axis=1)
        new[f"{PREFIX}{family}__nmiss"] = np.isnan(block).sum(axis=1).astype(float)

        # Short window versus long window: the direction of travel.
        short = [c for c in cols if parsed[c].window_days
                 and min(parsed[c].window_days) <= 7]
        long_ = [c for c in cols if parsed[c].window_days
                 and max(parsed[c].window_days) >= 31]
        if short and long_:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                s = np.nanmean(numeric[short].to_numpy(dtype=float), axis=1)
                l = np.nanmean(numeric[long_].to_numpy(dtype=float), axis=1)
            new[f"{PREFIX}{family}__accel"] = _safe_ratio(s, l)

    # ---- channel concentration -------------------------------------------
    by_channel: dict[str, list[str]] = {}
    for col, p in parsed.items():
        if p.channel and p.measure == "AMT" and p.aggregation in ("RAW", "TOT", "AVG"):
            by_channel.setdefault(p.channel, []).append(col)

    if len(by_channel) >= 2:
        totals = {}
        for ch, cols in by_channel.items():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                totals[ch] = np.nan_to_num(
                    np.nanmean(numeric[cols].to_numpy(dtype=float), axis=1))
        stack = np.column_stack(list(totals.values()))
        grand = stack.sum(axis=1, keepdims=True)
        share = np.divide(stack, grand, out=np.zeros_like(stack),
                          where=np.abs(grand) > 1e-9)
        new[f"{PREFIX}channel_top_share"] = share.max(axis=1)
        new[f"{PREFIX}channel_n_active"] = (share > 0.01).sum(axis=1).astype(float)
        # Herfindahl: 1.0 means every rupee moved through a single channel.
        new[f"{PREFIX}channel_hhi"] = (share ** 2).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            ent = -(share * np.log(np.where(share > 0, share, 1.0))).sum(axis=1)
        new[f"{PREFIX}channel_entropy"] = ent

    # ---- customer- versus bank-induced -----------------------------------
    ci = [c for c, p in parsed.items() if p.inducer == "CI"]
    bi = [c for c, p in parsed.items() if p.inducer == "BI"]
    if ci and bi:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ci_m = np.nan_to_num(np.nanmean(numeric[ci].to_numpy(dtype=float), axis=1))
            bi_m = np.nan_to_num(np.nanmean(numeric[bi].to_numpy(dtype=float), axis=1))
        new[f"{PREFIX}customer_induced_share"] = _safe_ratio(ci_m, ci_m + bi_m)

    # ---- credit versus debit balance -------------------------------------
    cr = [c for c, p in parsed.items() if p.direction == "CR" and p.measure == "AMT"]
    db = [c for c, p in parsed.items() if p.direction == "DB" and p.measure == "AMT"]
    if cr and db:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            cr_m = np.nan_to_num(np.nanmean(numeric[cr].to_numpy(dtype=float), axis=1))
            db_m = np.nan_to_num(np.nanmean(numeric[db].to_numpy(dtype=float), axis=1))
        # A pass-through account debits almost exactly what it credits.
        new[f"{PREFIX}credit_debit_ratio"] = _safe_ratio(db_m, cr_m)
        new[f"{PREFIX}net_flow_share"] = _safe_ratio(cr_m - db_m, cr_m + db_m)

    # ---- overall missingness ---------------------------------------------
    block = numeric.to_numpy(dtype=float)
    new[f"{PREFIX}row_nmiss"] = np.isnan(block).sum(axis=1).astype(float)
    new[f"{PREFIX}row_nmiss_frac"] = new[f"{PREFIX}row_nmiss"] / max(block.shape[1], 1)
    new[f"{PREFIX}row_nonzero_frac"] = (
        np.nan_to_num(np.abs(block)) > 1e-9).mean(axis=1)

    if not new:
        return out
    derived = pd.DataFrame(new, index=X.index).replace([np.inf, -np.inf], np.nan)
    return pd.concat([out, derived.astype(np.float32)], axis=1)


def engineered_columns(columns) -> list[str]:
    return [c for c in columns if str(c).startswith(PREFIX)]


__all__ = ["build", "engineered_columns", "PREFIX"]
