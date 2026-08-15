"""A stand-in dataset with the organisers' exact column schema.

The organisers published the data dictionary before the data. This module
generates a table with **every one of the 3,924 declared columns**, with values
whose distributions match what each column's grammar implies - ratios centred
near 1, deviations centred near 0, counts non-negative and integral, amounts
heavy-tailed, flags binary, categoricals categorical.

Its purpose is engineering, not science: it lets the whole pipeline be written,
tested and benchmarked now, and it means the moment the real file arrives the
only thing that changes is the path. Numbers produced from it are labelled as
such everywhere and must never be quoted as model performance.

A deliberate, modest signal is injected through the eighteen variables the bank
itself finalised, plus a handful of interactions, so the pipeline has something
real to find and feature selection can be exercised end to end.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from bodhi.boi.schema import TARGET, load_dictionary, parse_feature

# Categorical domains, mirroring the descriptions in the dictionary.
OCCUPATIONS = ["SAL", "SEMP", "BUS", "AGRI", "STUD", "HOUSE", "RETD", "PROF", "OTH"]
GENDERS = ["M", "F", "O"]
AREAS = ["METRO", "URBAN", "SEMI_URBAN", "RURAL"]
SEGMENTS = ["MASS", "AFFLUENT", "PREMIUM", "HNI", "NRI"]
PRODUCTS = ["SB_REGULAR", "SB_SALARY", "SB_BSBDA", "CA_REGULAR", "SB_MINOR", "CA_SME"]
ACCT_OPN_BUCKETS = ["<30D", "30-90D", "90-180D", "180-365D", "1-3Y", "3-5Y", ">5Y"]

ALERT_FLAGS = (
    "HIGH_VALUE_UPI_DB_TXNS", "MULTI_DBS_FROM_ACCOUNT", "MULTI_PG_TXNS",
    "PWD_CHANGED_LARGE_FUND_XFERS", "RCVING_FUNDS_FROM_MULITPLE_USERS",
    "RISKY_COUNTRY_TXNS", "STATUS_CHANGE_AFTER_WD", "TXN_AT_UNUSUAL_TIME",
    "MULTI_UPI_DB_TXNS", "FAILED_UPI_TXNS", "ONE_TO_MANY_UPI_PAYMENTS",
    "OTHER_ALERT_TYPES",
)
RESOLUTION_FLAGS = ("FRAUD_SUSPECTED", "OTHER_RESOLUTION", "FALSE_POSITIVE",
                    "UNATTENDED")
RISK_FLAGS = ("L1_FLG", "L2_FLG", "L3_FLG")


@dataclass
class SynthConfig:
    n_rows: int = 5_000
    fraud_rate: float = 0.06        # alert-level fraud rates are far from 1%
    missing_rate: float = 0.04      # real extracts are not dense
    seed: int = 20260817


def _column_values(col: str, n: int, rng: np.random.Generator) -> np.ndarray:
    """Draw a column whose distribution matches what its name implies."""
    p = parse_feature(col)

    if p.measure == "AMT":
        base = np.exp(rng.normal(8.0, 2.0, n))
    elif p.measure == "BAL":
        base = np.exp(rng.normal(9.5, 1.6, n))
    elif p.measure == "TXN":
        base = rng.poisson(2.2, n).astype(float)
    else:
        base = rng.gamma(2.0, 1.0, n)

    agg = p.aggregation
    if agg in ("R", "RA"):
        # A ratio of two windows: positive, piled up around 1, long right tail.
        out = np.exp(rng.normal(0.0, 0.65, n))
        out[rng.random(n) < 0.15] = 0.0          # no activity in the denominator window
        return out
    if agg in ("D", "DA", "D_TA"):
        # A deviation: signed, centred on zero.
        scale = np.maximum(base.std(), 1.0)
        return rng.normal(0.0, scale * 0.35, n)
    if agg in ("MIN",):
        return np.floor(base * rng.uniform(0.0, 0.6, n))
    if agg in ("MAX",):
        return np.ceil(base * rng.uniform(1.2, 3.0, n))
    if agg in ("AVG",):
        return base * rng.uniform(0.7, 1.3, n)
    if agg in ("TOT", "CNT", "RAW"):
        return base if p.measure != "TXN" else np.round(base)
    return base


def generate(config: SynthConfig | None = None) -> pd.DataFrame:
    """Build a table with the organisers' exact schema."""
    cfg = config or SynthConfig()
    rng = np.random.default_rng(cfg.seed)
    n = cfg.n_rows
    dd = load_dictionary()

    static_handled = {
        "MNTH", "PRODUCT_NAME", "TENURE_AS_OF_ALERT", "ACCT_OPN_DATE",
        "ACCT_OPN_DAYS", "AREA_CATEGORY", "CUST_OCCP", "GENDER",
        "SEGMENTATION_CLASS", "AGE_IN_YRS", "MIN_INC_SCORE", "MAX_INC_SCORE",
        "CNT_INC_SCR_GT650", "MIN_RESOLVE_DAYS", "MAX_RESOLVE_DAYS",
        "COUNT_ALERTS", "MORNING_ALERTS", "AFTERNOON_ALERTS", "EVENING_ALERTS",
        "NIGHT_ALERTS", *ALERT_FLAGS, *RESOLUTION_FLAGS, *RISK_FLAGS, TARGET,
    }

    data: dict[str, np.ndarray] = {}
    for col in dd.feature_columns:
        if col in static_handled:
            continue
        data[col] = _column_values(col, n, rng).astype(np.float32)

    # ---- demographics and alert metadata --------------------------------
    data["MNTH"] = rng.integers(1, 13, n).astype(np.float32)
    data["AGE_IN_YRS"] = np.clip(rng.normal(38, 13, n), 18, 88).astype(np.float32)
    data["GENDER"] = rng.choice(GENDERS, n, p=[0.62, 0.37, 0.01])
    data["CUST_OCCP"] = rng.choice(OCCUPATIONS, n)
    data["AREA_CATEGORY"] = rng.choice(AREAS, n, p=[0.28, 0.32, 0.24, 0.16])
    data["SEGMENTATION_CLASS"] = rng.choice(SEGMENTS, n, p=[0.6, 0.2, 0.12, 0.05, 0.03])
    data["PRODUCT_NAME"] = rng.choice(PRODUCTS, n)
    data["ACCT_OPN_DAYS"] = rng.choice(ACCT_OPN_BUCKETS, n,
                                       p=[0.06, 0.10, 0.12, 0.16, 0.26, 0.16, 0.14])
    tenure = np.clip(rng.gamma(2.0, 700, n), 5, 12_000)
    data["TENURE_AS_OF_ALERT"] = tenure.astype(np.float32)
    data["ACCT_OPN_DATE"] = pd.to_datetime("2026-07-01") - pd.to_timedelta(
        tenure.astype(int), unit="D")

    data["MIN_INC_SCORE"] = rng.uniform(200, 700, n).astype(np.float32)
    data["MAX_INC_SCORE"] = (data["MIN_INC_SCORE"] +
                             rng.uniform(0, 300, n)).astype(np.float32)
    data["CNT_INC_SCR_GT650"] = rng.poisson(0.6, n).astype(np.float32)
    data["COUNT_ALERTS"] = (1 + rng.poisson(1.4, n)).astype(np.float32)
    parts = rng.dirichlet([1.0, 1.4, 1.3, 0.7], n) * data["COUNT_ALERTS"][:, None]
    for i, c in enumerate(("MORNING_ALERTS", "AFTERNOON_ALERTS",
                           "EVENING_ALERTS", "NIGHT_ALERTS")):
        data[c] = np.round(parts[:, i]).astype(np.float32)
    for i, c in enumerate(RISK_FLAGS):
        data[c] = (rng.random(n) < (0.30, 0.14, 0.06)[i]).astype(np.float32)
    for c in ALERT_FLAGS:
        data[c] = (rng.random(n) < 0.11).astype(np.float32)

    frame = pd.DataFrame(data)

    # ---- inject a modest, learnable signal -------------------------------
    # Drivers are the bank's own finalised variables plus alert metadata, which
    # is where a real signal would live. The effect sizes are deliberately
    # moderate: a synthetic problem that is trivially separable teaches the
    # pipeline nothing about feature selection.
    logit = np.full(n, -2.9)
    drivers = [c for c in dd.bank_finalized if c in frame.columns
               and pd.api.types.is_numeric_dtype(frame[c])]
    for i, col in enumerate(drivers):
        v = frame[col].to_numpy(dtype=float)
        z = (v - np.nanmean(v)) / (np.nanstd(v) + 1e-9)
        logit += (0.34 if i % 2 == 0 else -0.26) * np.clip(z, -4, 4)

    logit += 0.55 * frame["NIGHT_ALERTS"].to_numpy()
    logit += 0.40 * frame["L3_FLG"].to_numpy()
    logit += 0.45 * frame["RCVING_FUNDS_FROM_MULITPLE_USERS"].to_numpy()
    logit += 0.35 * frame["MULTI_UPI_DB_TXNS"].to_numpy()
    logit -= 0.30 * (frame["SEGMENTATION_CLASS"] == "PREMIUM").to_numpy()
    logit += 0.50 * (frame["ACCT_OPN_DAYS"].isin(["<30D", "30-90D"])).to_numpy()
    # An interaction only a non-linear model can exploit.
    logit += 0.6 * ((frame["AGE_IN_YRS"] < 26).to_numpy() &
                    (frame["NIGHT_ALERTS"] > 0).to_numpy())
    logit += rng.normal(0, 1.15, n)

    prob = 1 / (1 + np.exp(-logit))
    target = (rng.random(n) < prob).astype(int)
    # Rebalance to the requested prevalence without destroying the ranking.
    order = np.argsort(-prob)
    want = int(round(cfg.fraud_rate * n))
    target = np.zeros(n, dtype=int)
    chosen = order[:want * 3]
    target[rng.choice(chosen, size=want, replace=False)] = 1

    # ---- resolution flags are downstream of the target -------------------
    # This is exactly the leakage the schema module quarantines: an analyst
    # sets these only after working the alert.
    frame["FRAUD_SUSPECTED"] = np.where(
        target == 1, (rng.random(n) < 0.93), (rng.random(n) < 0.03)).astype(np.float32)
    frame["FALSE_POSITIVE"] = np.where(
        target == 1, (rng.random(n) < 0.05), (rng.random(n) < 0.82)).astype(np.float32)
    frame["OTHER_RESOLUTION"] = (rng.random(n) < 0.10).astype(np.float32)
    frame["UNATTENDED"] = ((frame["FRAUD_SUSPECTED"] == 0) &
                           (frame["FALSE_POSITIVE"] == 0)).astype(np.float32)
    frame["MIN_RESOLVE_DAYS"] = np.where(
        target == 1, rng.gamma(3, 2.4, n), rng.gamma(2, 1.1, n)).astype(np.float32)
    frame["MAX_RESOLVE_DAYS"] = (frame["MIN_RESOLVE_DAYS"] +
                                 rng.gamma(2, 1.6, n)).astype(np.float32)

    frame[TARGET] = target

    # ---- realistic sparsity ----------------------------------------------
    numeric = [c for c in frame.columns
               if c != TARGET and pd.api.types.is_numeric_dtype(frame[c])]
    block = frame[numeric].to_numpy(dtype=np.float32, copy=True)
    holes = rng.random(block.shape) < cfg.missing_rate
    block[holes] = np.nan
    frame[numeric] = block

    return frame.reindex(columns=dd.all_columns)


def save(frame: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_parquet(path, index=False)
    return path


__all__ = ["SynthConfig", "generate", "save", "OCCUPATIONS", "ALERT_FLAGS"]
