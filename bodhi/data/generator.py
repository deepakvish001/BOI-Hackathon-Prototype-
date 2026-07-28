"""Simulator for a retail bank's payment traffic, with mule rings embedded.

Why a simulator? Real mule data cannot leave a bank, and no public dataset
carries *account-level* mule labels together with device, IP and UPI linkage.
So the prototype ships a generator that reproduces the structures the detector
must find, plus the structures that make naive rule engines fail:

* legitimate small businesses with heavy fan-in,
* legitimate dormant accounts that wake up,
* legitimate near-threshold payments (property, tuition, jewellery),
* families and CGNAT pools that share devices and IP addresses.

Without those "hard negatives" any model scores ~1.0 AUC and the evaluation is
worthless. With them, precision at a fixed alert budget becomes the honest
metric - which is exactly what an AML desk cares about.

Everything is driven by one seed, so `make data` is byte-reproducible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bodhi.config import DATA, DATA_DIR, DataConfig
from bodhi.data.typologies import TYPOLOGIES

# Node used as the sink for physical cash leaving the banking system.
CASH_POOL = "CASH_POOL"

SIM_END = datetime(2026, 7, 1, tzinfo=timezone.utc)

STATES = ("MH", "DL", "UP", "KA", "TN", "WB", "GJ", "RJ", "BR", "TG", "KL", "PB")

# Hour-of-day intensity for genuine retail payments: morning + evening peaks,
# a thin overnight tail.
_HOUR_WEIGHTS = np.array(
    [0.6, 0.35, 0.25, 0.2, 0.25, 0.6, 1.4, 2.6, 4.2, 5.6, 6.4, 6.6,
     6.2, 5.4, 5.0, 5.2, 5.8, 6.6, 7.4, 7.8, 6.8, 4.6, 2.6, 1.3],
    dtype=float,
)
_HOUR_P = _HOUR_WEIGHTS / _HOUR_WEIGHTS.sum()

# Median transaction size and daily transaction rate by income band.
_BAND_MEDIAN_AMT = {"LOW": 620.0, "MID": 2_100.0, "HIGH": 7_800.0}
_BAND_RATE = {"LOW": 0.16, "MID": 0.31, "HIGH": 0.62}
_BAND_P = np.array([0.46, 0.39, 0.15])
_BANDS = np.array(["LOW", "MID", "HIGH"])


@dataclass
class SimulationResult:
    """Every table the pipeline needs, plus the ring ground truth."""

    accounts: pd.DataFrame
    transactions: pd.DataFrame
    devices: pd.DataFrame
    fraud_alerts: pd.DataFrame
    iocs: pd.DataFrame
    regulatory: pd.DataFrame
    rings: pd.DataFrame
    meta: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "accounts": int(len(self.accounts)),
            "mule_accounts": int(self.accounts["is_mule"].sum()),
            "mule_rate": round(float(self.accounts["is_mule"].mean()), 5),
            "transactions": int(len(self.transactions)),
            "fraud_transactions": int(self.transactions["is_fraud"].sum()),
            "fraud_txn_rate": round(float(self.transactions["is_fraud"].mean()), 5),
            "rings": int(len(self.rings)),
            "fraud_alerts": int(len(self.fraud_alerts)),
            "iocs": int(len(self.iocs)),
            "regulatory_items": int(len(self.regulatory)),
            **self.meta,
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _acct_id(i: int) -> str:
    return f"ACC{i:07d}"


def _lognormal(rng: np.random.Generator, median: float, sigma: float, size) -> np.ndarray:
    return np.exp(np.log(median) + sigma * rng.standard_normal(size))


def _channel_for(amount: np.ndarray, is_merchant_leg: np.ndarray,
                 rng: np.random.Generator) -> np.ndarray:
    """Pick a rail the way a customer's app would: by amount and by purpose."""
    u = rng.random(amount.shape[0])
    out = np.full(amount.shape[0], "UPI", dtype=object)
    # Retail merchant payments: mostly UPI, some card.
    out = np.where(is_merchant_leg & (u < 0.22), "CARD", out)
    # Person-to-person above the UPI comfort zone moves to IMPS.
    out = np.where((~is_merchant_leg) & (amount > 25_000) & (u < 0.55), "IMPS", out)
    out = np.where(amount > 100_000, np.where(u < 0.5, "IMPS", "NEFT"), out)
    out = np.where(amount > 500_000, "RTGS", out)
    out = np.where(is_merchant_leg & (amount < 200) & (u > 0.9), "WALLET", out)
    return out


def _split_amount(rng: np.random.Generator, total: float, parts: int,
                  jitter: float = 0.22) -> list[float]:
    """Split ``total`` into ``parts`` uneven, human-looking chunks."""
    if parts <= 1:
        return [round(total, 2)]
    w = np.abs(1.0 + jitter * rng.standard_normal(parts))
    w = np.maximum(w, 0.15)
    w = w / w.sum()
    vals = np.round(total * w, -1)  # money moves in tens
    vals = np.maximum(vals, 100.0)
    return [float(v) for v in vals]


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------


def _build_accounts(rng: np.random.Generator, cfg: DataConfig) -> pd.DataFrame:
    n = cfg.n_accounts
    idx = np.arange(n)

    band = rng.choice(_BANDS, size=n, p=_BAND_P)
    # Merchants occupy the tail of the id space so they are easy to slice.
    is_merchant = np.zeros(n, dtype=bool)
    is_merchant[n - cfg.n_merchants:] = True

    age = np.clip(rng.normal(37, 13, n), 18, 82).astype(int)
    # Account vintage: most accounts are old, a minority are freshly opened -
    # and freshly-opened accounts are where mules concentrate in reality.
    vintage_days = np.clip(_lognormal(rng, 1_400, 1.1, n), 5, 9_000)
    open_date = SIM_END - pd.to_timedelta(vintage_days + cfg.n_days, unit="D")

    kyc = rng.choice(["FULL", "VIDEO", "MIN"], size=n, p=[0.80, 0.14, 0.06])

    # Device pool: unique devices first, then deliberate sharing so that
    # "shared device" alone is never sufficient evidence.
    device_idx = np.empty(n, dtype=int)
    n_unique = min(cfg.n_devices, n)
    device_idx[:n_unique] = np.arange(n_unique)
    if n > n_unique:
        device_idx[n_unique:] = rng.integers(0, n_unique, n - n_unique)
    rng.shuffle(device_idx)

    # CGNAT / broadband pools: ~3 accounts per public IP on average.
    ip_idx = rng.integers(0, max(1, n // 3), n)

    rate = np.array([_BAND_RATE[b] for b in band]) * _lognormal(rng, 1.0, 0.55, n)
    rate = np.clip(rate, 0.02, 4.0)
    # Merchants transact far more often than individuals.
    rate = np.where(is_merchant, rate * 14.0, rate)

    median_amt = np.array([_BAND_MEDIAN_AMT[b] for b in band]) * _lognormal(rng, 1.0, 0.28, n)

    df = pd.DataFrame(
        {
            "account_id": [_acct_id(i) for i in idx],
            "customer_id": [f"CUS{i:07d}" for i in idx],
            "bank_code": "BKID",
            "branch_code": [f"{rng.integers(1, 900):04d}" for _ in idx],
            "open_date": open_date,
            "kyc_level": kyc,
            "customer_age": age,
            "income_band": band,
            "state": rng.choice(list(STATES), size=n),
            "is_internal": True,
            "is_merchant": is_merchant,
            "home_device": [f"DEV{d:07d}" for d in device_idx],
            "home_ip": [f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}" for i in ip_idx],
            "vpa": [f"user{i:07d}@bodhipay" for i in idx],
            "base_rate": rate,
            "median_amount": median_amt,
            "account_vintage_days": vintage_days.astype(int),
            "is_mule": False,
            "mule_typology": None,
            "mule_role": None,
            "ring_id": None,
            "is_small_business": False,
            "legit_dormant_wake": False,
            "is_legit_collector": False,
            "is_bc_agent": False,
        }
    )
    return df


# --------------------------------------------------------------------------
# genuine traffic
# --------------------------------------------------------------------------


def _build_legit_transactions(
    rng: np.random.Generator, cfg: DataConfig, accounts: pd.DataFrame, t0: float
) -> pd.DataFrame:
    n = len(accounts)
    rate = accounts["base_rate"].to_numpy()
    median_amt = accounts["median_amount"].to_numpy()
    is_merchant = accounts["is_merchant"].to_numpy()
    merchant_ids = np.flatnonzero(is_merchant)

    # Personal circle: the handful of people you actually pay. Stored CSR-style
    # so counterparty sampling stays fully vectorised.
    circle_sizes = rng.integers(3, 26, n)
    circle_ptr = np.concatenate([[0], np.cumsum(circle_sizes)]).astype(int)
    circle_members = rng.integers(0, n, circle_ptr[-1])

    n_txn = rng.poisson(rate * cfg.n_days)
    n_txn = np.where(is_merchant, 0, n_txn)  # merchants receive, they do not push
    src = np.repeat(np.arange(n), n_txn)
    T = int(src.size)

    day = rng.integers(0, cfg.n_days, T)
    hour = rng.choice(24, T, p=_HOUR_P)
    ts = t0 + day * 86_400 + hour * 3_600 + rng.integers(0, 3_600, T)

    to_merchant = rng.random(T) < 0.47
    k = (rng.random(T) * circle_sizes[src]).astype(int)
    dst_circle = circle_members[circle_ptr[src] + k]
    dst_merchant = merchant_ids[rng.integers(0, merchant_ids.size, T)]
    dst = np.where(to_merchant, dst_merchant, dst_circle)
    dst = np.where(dst == src, (dst + 1) % n, dst)  # no self-payments

    amount = _lognormal(rng, 1.0, 0.85, T) * median_amt[src]
    amount = np.where(to_merchant, amount * 0.55, amount)
    amount = np.round(np.clip(amount, 10.0, 2_000_000.0), 2)

    channel = _channel_for(amount, to_merchant, rng)
    txn_type = np.where(to_merchant, "P2M", "P2P")

    # Devices: normally your own handset, occasionally a family member's.
    home_device = accounts["home_device"].to_numpy()
    dev = home_device[src].copy()
    swap = rng.random(T) < 0.04
    dev[swap] = home_device[rng.integers(0, n, int(swap.sum()))]

    ip = accounts["home_ip"].to_numpy()[src].copy()
    roam = rng.random(T) < 0.12
    ip[roam] = np.array(
        [f"49.{a}.{b}.{c}" for a, b, c in
         rng.integers(0, 256, (int(roam.sum()), 3))]
    )

    # A payee you have paid before is a weak but real safety signal.
    ben_age = np.where(
        to_merchant,
        rng.uniform(0.5, 24 * 200, T),
        rng.uniform(24, 24 * 120, T),
    )
    first_time = rng.random(T) < 0.09
    ben_age = np.where(first_time, rng.uniform(0.05, 20, T), ben_age)

    legit = pd.DataFrame(
        {
            "ts": ts.astype(np.float64),
            "src_idx": src,
            "dst_idx": dst,
            "amount": amount,
            "channel": channel,
            "txn_type": txn_type,
            "device_id": dev,
            "ip_address": ip,
            "beneficiary_age_hours": np.round(ben_age, 2),
            "is_fraud": False,
        }
    )

    extras = [
        _salary_credits(rng, cfg, accounts, t0),
        _cash_withdrawals(rng, cfg, accounts, t0),
    ]
    return pd.concat([legit, *extras], ignore_index=True)


def _salary_credits(rng, cfg: DataConfig, accounts: pd.DataFrame, t0: float) -> pd.DataFrame:
    """Monthly employer payouts - legitimate one-to-many fan-out."""
    n = len(accounts)
    is_merchant = accounts["is_merchant"].to_numpy()
    employers = np.flatnonzero(is_merchant)[: max(1, cfg.n_merchants // 3)]
    salaried = np.flatnonzero((~is_merchant) & (rng.random(n) < 0.42))
    months = max(1, cfg.n_days // 30)

    src = np.tile(employers[rng.integers(0, employers.size, salaried.size)], months)
    dst = np.tile(salaried, months)
    month_idx = np.repeat(np.arange(months), salaried.size)
    # Paid on the 1st, within business hours, with a little jitter.
    ts = t0 + month_idx * 30 * 86_400 + 10 * 3_600 + rng.integers(0, 4 * 3_600, dst.size)

    band = accounts["income_band"].to_numpy()[dst]
    base = np.where(band == "HIGH", 145_000.0, np.where(band == "MID", 48_000.0, 19_000.0))
    amount = np.round(base * _lognormal(rng, 1.0, 0.18, dst.size), 2)

    return pd.DataFrame(
        {
            "ts": ts.astype(np.float64),
            "src_idx": src,
            "dst_idx": dst,
            "amount": amount,
            "channel": "NEFT",
            "txn_type": "SALARY",
            "device_id": None,
            "ip_address": None,
            "beneficiary_age_hours": 24 * 365.0,
            "is_fraud": False,
        }
    )


def _cash_withdrawals(rng, cfg: DataConfig, accounts: pd.DataFrame, t0: float) -> pd.DataFrame:
    """Ordinary ATM/AePS usage, so cash-out alone is not incriminating."""
    n = len(accounts)
    is_merchant = accounts["is_merchant"].to_numpy()
    rate = accounts["base_rate"].to_numpy() * 0.30
    counts = rng.poisson(np.where(is_merchant, 0.0, rate) * cfg.n_days)
    src = np.repeat(np.arange(n), counts)
    T = int(src.size)
    if T == 0:
        return pd.DataFrame(columns=["ts", "src_idx", "dst_idx", "amount", "channel",
                                     "txn_type", "device_id", "ip_address",
                                     "beneficiary_age_hours", "is_fraud"])

    day = rng.integers(0, cfg.n_days, T)
    hour = rng.choice(24, T, p=_HOUR_P)
    ts = t0 + day * 86_400 + hour * 3_600 + rng.integers(0, 3_600, T)
    # ATMs dispense in multiples of 100, capped by daily limits.
    amount = np.round(np.clip(_lognormal(rng, 3_000, 0.7, T), 100, 25_000), -2)
    channel = np.where(rng.random(T) < 0.12, "AEPS", "ATM")

    return pd.DataFrame(
        {
            "ts": ts.astype(np.float64),
            "src_idx": src,
            "dst_idx": -1,  # CASH_POOL
            "amount": amount,
            "channel": channel,
            "txn_type": "CASH_WITHDRAWAL",
            "device_id": None,
            "ip_address": None,
            "beneficiary_age_hours": None,
            "is_fraud": False,
        }
    )


def _build_hard_negatives(
    rng: np.random.Generator, cfg: DataConfig, accounts: pd.DataFrame, t0: float
) -> pd.DataFrame:
    """Legitimate accounts that *look* like mules to a rule engine."""
    n = len(accounts)
    non_merchant = np.flatnonzero(~accounts["is_merchant"].to_numpy())
    rows: list[pd.DataFrame] = []

    # (1) Unregistered small businesses: kirana stores, tuition teachers,
    #     cab drivers. Heavy P2P fan-in from strangers, all day, every day.
    n_biz = int(0.014 * n)
    biz = rng.choice(non_merchant, size=n_biz, replace=False)
    accounts.loc[biz, "is_small_business"] = True
    per_day = rng.integers(3, 16, n_biz)
    counts = per_day * cfg.n_days
    src_biz = np.repeat(biz, counts)
    T = int(src_biz.size)
    payers = rng.integers(0, n, T)
    ts = t0 + rng.integers(0, cfg.n_days, T) * 86_400 + rng.choice(24, T, p=_HOUR_P) * 3_600
    amt = np.round(np.clip(_lognormal(rng, 420, 0.85, T), 20, 30_000), 2)
    rows.append(
        pd.DataFrame(
            {
                "ts": ts.astype(np.float64),
                "src_idx": payers,
                "dst_idx": src_biz,
                "amount": amt,
                "channel": "UPI",
                "txn_type": "P2M",
                "device_id": accounts["home_device"].to_numpy()[payers],
                "ip_address": accounts["home_ip"].to_numpy()[payers],
                "beneficiary_age_hours": np.round(rng.uniform(0.1, 24 * 300, T), 2),
                "is_fraud": False,
            }
        )
    )

    # (2) Genuine near-threshold payments: property instalments, jewellery,
    #     school fees - repeatedly parked just under the UPI cap.
    n_thr = int(0.008 * n)
    thr = rng.choice(np.setdiff1d(non_merchant, biz), size=n_thr, replace=False)
    reps = rng.integers(3, 12, n_thr)
    src_thr = np.repeat(thr, reps)
    T2 = int(src_thr.size)
    ts2 = t0 + rng.integers(0, cfg.n_days, T2) * 86_400 + rng.integers(9, 19, T2) * 3_600
    amt2 = np.round(rng.uniform(44_000, 49_900, T2), -2)
    rows.append(
        pd.DataFrame(
            {
                "ts": ts2.astype(np.float64),
                "src_idx": src_thr,
                "dst_idx": rng.integers(0, n, T2),
                "amount": amt2,
                "channel": "IMPS",
                "txn_type": "P2P",
                "device_id": accounts["home_device"].to_numpy()[src_thr],
                "ip_address": accounts["home_ip"].to_numpy()[src_thr],
                "beneficiary_age_hours": np.round(rng.uniform(1, 24 * 60, T2), 2),
                "is_fraud": False,
            }
        )
    )

    # (3) Genuine dormancy break: an NRI or a student account waking up.
    n_wake = int(0.014 * n)
    wake = rng.choice(np.setdiff1d(non_merchant, np.concatenate([biz, thr])),
                      size=n_wake, replace=False)
    accounts.loc[wake, "legit_dormant_wake"] = True
    burst = rng.integers(6, 30, n_wake)
    src_w = np.repeat(wake, burst)
    T3 = int(src_w.size)
    wake_day = np.repeat(rng.integers(int(cfg.n_days * 0.72), cfg.n_days - 1, n_wake), burst)
    ts3 = t0 + wake_day * 86_400 + rng.integers(0, 3 * 86_400, T3)
    amt3 = np.round(np.clip(_lognormal(rng, 3_600, 0.9, T3), 50, 250_000), 2)
    rows.append(
        pd.DataFrame(
            {
                "ts": ts3.astype(np.float64),
                "src_idx": src_w,
                "dst_idx": rng.integers(0, n, T3),
                "amount": amt3,
                "channel": "UPI",
                "txn_type": "P2P",
                "device_id": accounts["home_device"].to_numpy()[src_w],
                "ip_address": accounts["home_ip"].to_numpy()[src_w],
                "beneficiary_age_hours": np.round(rng.uniform(0.2, 48, T3), 2),
                "is_fraud": False,
            }
        )
    )

    # (4) Community collectors: chit-fund organisers, tuition-batch collectors,
    #     wedding/festival committees, temple donations. They receive dozens of
    #     payments from strangers in a burst, then forward almost everything to
    #     one person within hours. Structurally identical to a collection mule;
    #     the difference is intent, which no feature can observe. This is the
    #     hard negative that decides whether the precision number is honest.
    used = np.concatenate([biz, thr, wake])
    n_coll = int(0.009 * n)
    coll = rng.choice(np.setdiff1d(non_merchant, used), size=n_coll, replace=False)
    accounts.loc[coll, "is_legit_collector"] = True
    coll_rows_in, coll_rows_out = [], []
    for a in coll:
        for _ in range(int(rng.integers(2, 7))):
            t_ev = t0 + rng.integers(2, cfg.n_days - 1) * 86_400 + \
                rng.integers(8, 20) * 3_600
            k = int(rng.integers(14, 42))
            payers = rng.integers(0, n, k)
            amts = np.round(np.clip(_lognormal(rng, 1_800, 0.8, k), 100, 60_000), -1)
            coll_rows_in.append(pd.DataFrame({
                "ts": t_ev + rng.uniform(0, 5 * 3_600, k),
                "src_idx": payers, "dst_idx": int(a), "amount": amts,
                "channel": "UPI", "txn_type": "P2P",
                "device_id": accounts["home_device"].to_numpy()[payers],
                "ip_address": accounts["home_ip"].to_numpy()[payers],
                # Payers who have never paid this organiser before.
                "beneficiary_age_hours": np.round(rng.uniform(0.02, 3.0, k), 3),
                "is_fraud": False,
            }))
            fwd = float(amts.sum()) * rng.uniform(0.82, 0.97)
            parts = _split_amount(rng, fwd, int(rng.integers(1, 4)))
            coll_rows_out.append(pd.DataFrame({
                "ts": t_ev + rng.uniform(5 * 3_600, 14 * 3_600, len(parts)),
                "src_idx": int(a), "dst_idx": rng.integers(0, n, len(parts)),
                "amount": parts,
                "channel": ["IMPS" if p > 100_000 else "UPI" for p in parts],
                "txn_type": "P2P",
                "device_id": accounts["home_device"].to_numpy()[a],
                "ip_address": accounts["home_ip"].to_numpy()[a],
                "beneficiary_age_hours": np.round(rng.uniform(0.5, 900, len(parts)), 2),
                "is_fraud": False,
            }))
    rows.extend(coll_rows_in)
    rows.extend(coll_rows_out)

    # (5) Business-Correspondent (Bank Mitra) agents. A BC agent legitimately
    #     operates a single handheld device on behalf of many village customers
    #     and dispenses cash via AePS all day - the exact fingerprint of a
    #     device farm doing rapid cash-out.
    used = np.concatenate([used, coll])
    n_agents = max(3, int(0.0016 * n))
    bc_rows = []
    for j in range(n_agents):
        size = int(rng.integers(8, 22))
        members = rng.choice(np.setdiff1d(non_merchant, used), size=size, replace=False)
        used = np.concatenate([used, members])
        accounts.loc[members, "is_bc_agent"] = True
        dev = f"BCDEV{j:04d}"
        accounts.loc[members, "home_device"] = dev
        k = size * int(rng.integers(4, 14))
        src_bc = rng.choice(members, size=k)
        bc_rows.append(pd.DataFrame({
            "ts": t0 + rng.integers(0, cfg.n_days, k) * 86_400 +
                  rng.integers(8, 19, k) * 3_600,
            "src_idx": src_bc, "dst_idx": -1,
            "amount": np.round(np.clip(_lognormal(rng, 2_200, 0.7, k), 100, 10_000), -2),
            "channel": "AEPS", "txn_type": "CASH_WITHDRAWAL",
            "device_id": dev, "ip_address": None,
            "beneficiary_age_hours": None, "is_fraud": False,
        }))
    rows.extend(bc_rows)

    return pd.concat(rows, ignore_index=True)


# --------------------------------------------------------------------------
# mule rings
# --------------------------------------------------------------------------


def _build_rings(
    rng: np.random.Generator, cfg: DataConfig, accounts: pd.DataFrame, t0: float
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Instantiate mule rings and emit their transaction flows.

    Returns (fraud transactions, ring table, victim-report seeds).
    """
    n = len(accounts)
    n_mules_target = int(round(cfg.mule_rate * n))
    eligible = np.flatnonzero(
        (~accounts["is_merchant"].to_numpy())
        & (~accounts["is_small_business"].to_numpy())
        & (~accounts["legit_dormant_wake"].to_numpy())
        & (~accounts["is_legit_collector"].to_numpy())
        & (~accounts["is_bc_agent"].to_numpy())
    )
    rng.shuffle(eligible)
    pool = list(eligible)

    weights = np.array([t.weight for t in TYPOLOGIES])
    weights = weights / weights.sum()

    home_device = accounts["home_device"].to_numpy()
    home_ip = accounts["home_ip"].to_numpy()

    rows: list[dict[str, Any]] = []
    rings: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    assigned = 0
    ring_no = 0

    # A quarter of rings only switch on in the final window: these are the
    # out-of-time test cases the model has genuinely never seen.
    late_cut_day = int(cfg.n_days * (1 - cfg.test_fraction))

    while assigned < n_mules_target and len(pool) > 25:
        typ = TYPOLOGIES[rng.choice(len(TYPOLOGIES), p=weights)]
        size = int(rng.integers(typ.size_range[0], typ.size_range[1] + 1))
        size = min(size, len(pool))
        members = [pool.pop() for _ in range(size)]
        assigned += size
        ring_no += 1
        ring_id = f"RING{ring_no:04d}"

        # Role assignment: collectors take victim money, relays launder it,
        # cash-out accounts convert it to notes.
        n_coll = max(1, int(round(size * 0.4)))
        n_cash = max(1, int(round(size * 0.25)))
        n_relay = max(0, size - n_coll - n_cash)
        collectors = members[:n_coll]
        relays = members[n_coll:n_coll + n_relay]
        cashouts = members[n_coll + n_relay:]
        if not cashouts:
            cashouts = [members[-1]]

        is_late = rng.random() < cfg.test_fraction
        if is_late:
            day_lo, day_hi = late_cut_day, cfg.n_days - 2
        else:
            day_lo, day_hi = 12, max(13, late_cut_day - 2)
        if day_hi <= day_lo:
            day_hi = day_lo + 1

        # "Quiet" rings run a single small campaign - the hard positives.
        quiet = rng.random() < 0.18
        n_campaigns = 1 if quiet else int(rng.integers(1, 5))

        ring_devices = [f"DEVFARM{ring_no:04d}_{j}" for j in range(rng.integers(1, 3))]
        ring_ip = f"103.{rng.integers(0,256)}.{rng.integers(0,256)}.{rng.integers(0,256)}"

        first_day = day_hi
        ring_amount = 0.0

        for _ in range(n_campaigns):
            c_day = int(rng.integers(day_lo, day_hi))
            first_day = min(first_day, c_day)
            # Campaigns run in the small hours or late evening more often.
            c_ts = t0 + c_day * 86_400 + float(rng.choice(
                [1, 2, 3, 4, 11, 14, 20, 21, 22, 23],
                p=[.10, .10, .09, .08, .08, .08, .09, .12, .14, .12])) * 3_600

            n_victims = {
                "FAN_IN_AGGREGATION": (22, 60),
                "SMURFING": (10, 26),
                "LAYERING_CHAIN": (4, 12),
                "DORMANT_BURST": (8, 24),
                "DEVICE_FARM": (10, 30),
                "RAPID_CASHOUT": (6, 18),
                "APK_HARVEST": (12, 34),
            }[typ.code]
            n_vic = int(rng.integers(*n_victims))
            if quiet:
                n_vic = max(3, n_vic // 3)

            victims = rng.choice(n, size=n_vic, replace=False)
            collected: dict[int, float] = {}

            # --- Stage 1: victims -> collectors -----------------------------
            for v in victims:
                coll = collectors[int(rng.integers(0, len(collectors)))]
                if typ.code == "SMURFING":
                    amt = float(np.round(rng.uniform(45_000, 49_800), -2))
                else:
                    amt = float(np.round(np.clip(
                        _lognormal(rng, 24_000, 1.0, 1)[0], 1_500, 480_000), -1))
                # Victim pays within a six-hour social-engineering window.
                ts = c_ts + rng.uniform(0, 6 * 3_600)
                # Usually a payee added minutes ago - but a quarter of victims
                # were groomed for days first, so this signal cannot be relied
                # on alone.
                ben_age = (float(np.round(rng.uniform(0.01, 1.5), 3))
                           if rng.random() < 0.75
                           else float(np.round(rng.uniform(24, 24 * 9), 2)))
                rows.append({
                    "ts": ts, "src_idx": int(v), "dst_idx": int(coll), "amount": amt,
                    "channel": "UPI" if amt <= 100_000 else "IMPS",
                    "txn_type": "P2P",
                    "device_id": home_device[v], "ip_address": home_ip[v],
                    "beneficiary_age_hours": ben_age,
                    "is_fraud": True, "ring_id": ring_id,
                })
                collected[coll] = collected.get(coll, 0.0) + amt
                ring_amount += amt
                reports.append({
                    "victim_idx": int(v), "beneficiary_idx": int(coll),
                    "amount": amt, "ts": ts, "ring_id": ring_id,
                    "category": "APK_TROJAN" if typ.code == "APK_HARVEST" else "UPI_FRAUD",
                })

            # --- Stage 2: collectors -> relays (layering) -------------------
            hops = 3 if typ.code == "LAYERING_CHAIN" else 1
            level_funds = collected
            for hop in range(hops):
                if not relays:
                    break
                nxt: dict[int, float] = {}
                for acct, total in level_funds.items():
                    cut = rng.uniform(0.02, 0.09)          # handler commission
                    fwd = total * (1 - cut)
                    if typ.code == "SMURFING":
                        parts = max(2, int(fwd // 47_000) + 1)
                    elif typ.code == "RAPID_CASHOUT":
                        parts = 1
                    else:
                        parts = int(rng.integers(1, 4))
                    for part in _split_amount(rng, fwd, parts):
                        tgt = relays[int(rng.integers(0, len(relays)))]
                        if tgt == acct:
                            continue
                        delay = rng.uniform(180, 3 * 3_600) if typ.code != "RAPID_CASHOUT" \
                            else rng.uniform(60, 900)
                        ts = c_ts + (hop + 1) * 900 + delay
                        amt = float(min(part, 199_000)) if part > 199_000 else float(part)
                        rows.append({
                            "ts": ts, "src_idx": int(acct), "dst_idx": int(tgt),
                            "amount": amt,
                            "channel": "IMPS" if amt > 100_000 else "UPI",
                            "txn_type": "P2P",
                            "device_id": ring_devices[int(rng.integers(0, len(ring_devices)))]
                            if typ.code == "DEVICE_FARM" else home_device[acct],
                            "ip_address": ring_ip if typ.code == "DEVICE_FARM" else home_ip[acct],
                            "beneficiary_age_hours": float(np.round(rng.uniform(0.02, 6), 3)),
                            "is_fraud": True, "ring_id": ring_id,
                        })
                        nxt[tgt] = nxt.get(tgt, 0.0) + amt
                level_funds = nxt

            # --- Stage 3: relays -> cash-out accounts -----------------------
            drain: dict[int, float] = {}
            for acct, total in level_funds.items():
                cut = rng.uniform(0.01, 0.05)
                fwd = total * (1 - cut)
                for part in _split_amount(rng, fwd, int(rng.integers(1, 3))):
                    tgt = cashouts[int(rng.integers(0, len(cashouts)))]
                    if tgt == acct:
                        continue
                    ts = c_ts + rng.uniform(1.5 * 3_600, 8 * 3_600)
                    rows.append({
                        "ts": ts, "src_idx": int(acct), "dst_idx": int(tgt),
                        "amount": float(part),
                        "channel": "IMPS" if part > 100_000 else "UPI",
                        "txn_type": "P2P",
                        "device_id": ring_devices[0] if typ.code == "DEVICE_FARM"
                        else home_device[acct],
                        "ip_address": ring_ip if typ.code == "DEVICE_FARM" else home_ip[acct],
                        "beneficiary_age_hours": float(np.round(rng.uniform(0.02, 12), 3)),
                        "is_fraud": True, "ring_id": ring_id,
                    })
                    drain[tgt] = drain.get(tgt, 0.0) + float(part)

            # --- Stage 4: cash leaves the banking system --------------------
            for acct, total in drain.items():
                remaining = total * rng.uniform(0.86, 0.99)
                # ATM daily limits force many small withdrawals - itself a signal.
                while remaining > 500:
                    chunk = float(min(remaining, np.round(rng.uniform(9_000, 25_000), -2)))
                    lag = rng.uniform(300, 2 * 3_600) if typ.code == "RAPID_CASHOUT" \
                        else rng.uniform(1_800, 30 * 3_600)
                    rows.append({
                        "ts": c_ts + 4 * 3_600 + lag, "src_idx": int(acct), "dst_idx": -1,
                        "amount": chunk,
                        "channel": "AEPS" if rng.random() < 0.38 else "ATM",
                        "txn_type": "CASH_WITHDRAWAL",
                        "device_id": None, "ip_address": None,
                        "beneficiary_age_hours": None,
                        "is_fraud": True, "ring_id": ring_id,
                    })
                    remaining -= chunk

        # Persist ring membership + roles onto the account table.
        for a in collectors:
            accounts.loc[a, ["is_mule", "mule_typology", "mule_role", "ring_id"]] = \
                [True, typ.code, "COLLECTOR", ring_id]
        for a in relays:
            accounts.loc[a, ["is_mule", "mule_typology", "mule_role", "ring_id"]] = \
                [True, typ.code, "RELAY", ring_id]
        for a in cashouts:
            accounts.loc[a, ["is_mule", "mule_typology", "mule_role", "ring_id"]] = \
                [True, typ.code, "CASHOUT", ring_id]

        # Typology-specific account-level fingerprints. Each is applied to a
        # *subset* of the ring: real syndicates are sloppy and inconsistent, and
        # a model trained on uniformly-marked mules learns the marking rather
        # than the behaviour.
        def _subset(frac: float) -> list:
            pick = [m for m in members if rng.random() < frac]
            return pick or [members[0]]

        if typ.code == "DEVICE_FARM":
            farm = _subset(0.75)
            accounts.loc[farm, "home_device"] = ring_devices[0]
            accounts.loc[farm, "home_ip"] = ring_ip
        if typ.code == "DORMANT_BURST":
            # Only some of the stable are freshly rented; the rest are the
            # handler's own long-lived accounts with ordinary history.
            accounts.loc[_subset(0.6), "base_rate"] = 0.0
        if typ.code in ("APK_HARVEST", "SMURFING"):
            # Accounts bought off the shelf: recently opened, minimum KYC.
            bought = _subset(0.6)
            accounts.loc[bought, "kyc_level"] = "MIN"
            accounts.loc[bought, "account_vintage_days"] = rng.integers(5, 70, len(bought))

        rings.append({
            "ring_id": ring_id,
            "typology": typ.code,
            "size": size,
            "members": ",".join(_acct_id(m) for m in members),
            "collectors": ",".join(_acct_id(m) for m in collectors),
            "cashouts": ",".join(_acct_id(m) for m in cashouts),
            "first_active_day": int(first_day),
            "is_late_ring": bool(is_late),
            "is_quiet": bool(quiet),
            "total_amount": round(ring_amount, 2),
            "n_campaigns": n_campaigns,
        })

    fraud_df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["ts", "src_idx", "dst_idx", "amount", "channel", "txn_type",
                 "device_id", "ip_address", "beneficiary_age_hours", "is_fraud", "ring_id"])
    return fraud_df, pd.DataFrame(rings), reports


# --------------------------------------------------------------------------
# intelligence feeds
# --------------------------------------------------------------------------


def _build_feeds(
    rng: np.random.Generator,
    cfg: DataConfig,
    accounts: pd.DataFrame,
    rings: pd.DataFrame,
    reports: list[dict[str, Any]],
    t0: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """NCRP tickets, APK-derived IoCs and regulatory directives.

    All three carry honest timestamps. Only ~38% of victims ever complain and
    complaints arrive hours-to-days late, so the feeds can never be the whole
    answer - which is the point of the ML layers.
    """
    ids = accounts["account_id"].to_numpy()
    vpas = accounts["vpa"].to_numpy()

    # ---- Government cyber-fraud tickets (NCRP / 1930) --------------------
    alert_rows = []
    for i, r in enumerate(reports):
        if rng.random() > 0.38:
            continue
        lag = rng.uniform(2 * 3_600, 5 * 86_400)
        alert_rows.append({
            "ticket_id": f"NCRP{i:08d}",
            "reported_at": r["ts"] + lag,
            "source": str(rng.choice(["NCRP", "HELPLINE_1930", "I4C", "PARTNER_BANK"],
                                     p=[0.45, 0.32, 0.13, 0.10])),
            "victim_account": ids[r["victim_idx"]],
            "beneficiary_account": ids[r["beneficiary_idx"]],
            "beneficiary_vpa": vpas[r["beneficiary_idx"]],
            "amount": r["amount"],
            "fraud_category": r["category"],
            "narrative": "Victim reports unauthorised debit after installing a "
                         "third-party application and sharing an OTP.",
            "ring_id": r["ring_id"],
        })

    # A realistic feed also carries mistaken complaints against innocent payees.
    n_noise = max(1, int(0.05 * max(len(alert_rows), 1)))
    innocent = rng.choice(len(accounts), size=n_noise, replace=False)
    for j, a in enumerate(innocent):
        alert_rows.append({
            "ticket_id": f"NCRPX{j:06d}",
            "reported_at": t0 + rng.uniform(0, cfg.n_days * 86_400),
            "source": "NCRP",
            "victim_account": ids[rng.integers(0, len(accounts))],
            "beneficiary_account": ids[a],
            "beneficiary_vpa": vpas[a],
            "amount": float(np.round(rng.uniform(500, 40_000), 2)),
            "fraud_category": "DISPUTED_TXN",
            "narrative": "Goods-not-delivered dispute; beneficiary contests the claim.",
            "ring_id": None,
        })
    fraud_alerts = pd.DataFrame(alert_rows)

    # ---- IoCs handed over by BODHI SHIELD AI ----------------------------
    ioc_rows = []
    families = ["Cerberus", "Anubis", "Hydra", "SpyNote", "Octo", "BankBot"]
    for _, ring in rings.iterrows():
        emit = ring["typology"] == "APK_HARVEST" or rng.random() < 0.28
        if not emit:
            continue
        sha = "".join(rng.choice(list("0123456789abcdef"), 64))
        family = str(rng.choice(families))
        # For APK campaigns the malware is caught days *before* the money moves.
        base_ts = t0 + ring["first_active_day"] * 86_400
        obs = base_ts - rng.uniform(1, 10) * 86_400 if ring["typology"] == "APK_HARVEST" \
            else base_ts + rng.uniform(0, 3) * 86_400
        for member in str(ring["collectors"]).split(","):
            if not member:
                continue
            k = int(member[3:])
            ioc_rows.append({
                "ioc_id": f"IOC{len(ioc_rows):07d}", "ioc_type": "UPI_VPA",
                "value": vpas[k], "confidence": float(np.round(rng.uniform(0.72, 0.98), 3)),
                "source_apk_sha256": sha, "malware_family": family,
                "observed_at": obs, "ring_id": ring["ring_id"],
            })
            if rng.random() < 0.6:
                ioc_rows.append({
                    "ioc_id": f"IOC{len(ioc_rows):07d}", "ioc_type": "ACCOUNT",
                    "value": ids[k], "confidence": float(np.round(rng.uniform(0.7, 0.95), 3)),
                    "source_apk_sha256": sha, "malware_family": family,
                    "observed_at": obs, "ring_id": ring["ring_id"],
                })
    # False-positive IoCs: sinkholed or recycled identifiers.
    for j in range(max(1, len(ioc_rows) // 20)):
        k = int(rng.integers(0, len(accounts)))
        ioc_rows.append({
            "ioc_id": f"IOCX{j:06d}", "ioc_type": "UPI_VPA", "value": vpas[k],
            "confidence": float(np.round(rng.uniform(0.30, 0.55), 3)),
            "source_apk_sha256": None, "malware_family": "Unclassified",
            "observed_at": t0 + rng.uniform(0, cfg.n_days * 86_400), "ring_id": None,
        })
    iocs = pd.DataFrame(ioc_rows)

    # ---- Regulatory / supervisory feed ----------------------------------
    known = rings[~rings["is_late_ring"]]
    listed: list[str] = []
    for _, ring in known.iterrows():
        if rng.random() < 0.22:
            listed.extend(str(ring["members"]).split(",")[:2])
    reg_rows = [
        {
            "feed_id": "RBI-ML-2026-014",
            "published_at": t0 + int(cfg.n_days * 0.55) * 86_400,
            "authority": "RBI",
            "directive_type": "MULE_ACCOUNT_LIST",
            "entities": ",".join(listed),
            "payload": json.dumps({"action": "ENHANCED_MONITORING", "count": len(listed)}),
        },
        {
            "feed_id": "NPCI-VEL-2026-003",
            "published_at": t0 + int(cfg.n_days * 0.30) * 86_400,
            "authority": "NPCI",
            "directive_type": "VELOCITY_LIMIT",
            "entities": "",
            "payload": json.dumps({"channel": "UPI", "max_p2p_per_day": 100000,
                                   "max_new_beneficiary_first_24h": 5000}),
        },
        {
            "feed_id": "CERTIN-ADV-2026-119",
            "published_at": t0 + int(cfg.n_days * 0.40) * 86_400,
            "authority": "CERT_IN",
            "directive_type": "TYPOLOGY",
            "entities": "",
            "payload": json.dumps({"typology": "APK_HARVEST",
                                   "indicator": "fresh beneficiary + MIN KYC + AePS cash-out"}),
        },
    ]
    regulatory = pd.DataFrame(reg_rows)
    return fraud_alerts, iocs, regulatory


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def generate(cfg: DataConfig | None = None) -> SimulationResult:
    """Run the full simulation and return every table, fully labelled."""
    cfg = cfg or DATA
    rng = np.random.default_rng(cfg.seed)
    t0 = (SIM_END - timedelta(days=cfg.n_days)).timestamp()

    accounts = _build_accounts(rng, cfg)
    hard_neg = _build_hard_negatives(rng, cfg, accounts, t0)
    fraud_df, rings, reports = _build_rings(rng, cfg, accounts, t0)
    legit = _build_legit_transactions(rng, cfg, accounts, t0)

    txns = pd.concat([legit, hard_neg, fraud_df], ignore_index=True)
    if "ring_id" not in txns.columns:
        txns["ring_id"] = None
    txns = txns[(txns["ts"] >= t0) & (txns["ts"] <= t0 + cfg.n_days * 86_400)]
    txns = txns.sort_values("ts", kind="mergesort").reset_index(drop=True)

    ids = accounts["account_id"].to_numpy()
    src_idx = txns["src_idx"].to_numpy()
    dst_idx = txns["dst_idx"].to_numpy()
    txns["src_account"] = ids[src_idx]
    txns["dst_account"] = np.where(dst_idx < 0, CASH_POOL, ids[np.maximum(dst_idx, 0)])
    txns["txn_id"] = [f"TXN{i:09d}" for i in range(len(txns))]
    txns["timestamp"] = pd.to_datetime(txns["ts"], unit="s", utc=True)
    txns["src_vpa"] = accounts["vpa"].to_numpy()[src_idx]
    txns["dst_vpa"] = np.where(dst_idx < 0, None,
                               accounts["vpa"].to_numpy()[np.maximum(dst_idx, 0)])
    txns["is_fraud"] = txns["is_fraud"].fillna(False).astype(bool)

    txns = txns[[
        "txn_id", "ts", "timestamp", "src_account", "dst_account", "amount",
        "channel", "txn_type", "device_id", "ip_address", "src_vpa", "dst_vpa",
        "beneficiary_age_hours", "is_fraud", "ring_id",
    ]]

    fraud_alerts, iocs, regulatory = _build_feeds(rng, cfg, accounts, rings, reports, t0)
    for frame, col in ((fraud_alerts, "reported_at"), (iocs, "observed_at"),
                       (regulatory, "published_at")):
        if len(frame):
            frame[col] = pd.to_datetime(frame[col], unit="s", utc=True)

    devices = (
        txns.loc[txns["device_id"].notna(), ["device_id", "src_account", "ts"]]
        .groupby(["device_id", "src_account"], as_index=False)
        .agg(first_seen=("ts", "min"), last_seen=("ts", "max"))
        .rename(columns={"src_account": "account_id"})
    )
    devices["first_seen"] = pd.to_datetime(devices["first_seen"], unit="s", utc=True)
    devices["last_seen"] = pd.to_datetime(devices["last_seen"], unit="s", utc=True)

    accounts = accounts.drop(columns=["base_rate", "median_amount"])
    accounts["is_mule"] = accounts["is_mule"].astype(bool)

    meta = {
        "seed": cfg.seed,
        "n_days": cfg.n_days,
        "sim_start": pd.Timestamp(t0, unit="s", tz="UTC").isoformat(),
        "sim_end": pd.Timestamp(t0 + cfg.n_days * 86_400, unit="s", tz="UTC").isoformat(),
        "oot_cut_ts": float(t0 + cfg.n_days * (1 - cfg.test_fraction) * 86_400),
        "late_rings": int(rings["is_late_ring"].sum()) if len(rings) else 0,
    }
    return SimulationResult(accounts, txns, devices, fraud_alerts, iocs,
                            regulatory, rings, meta)


_TABLES = ("accounts", "transactions", "devices", "fraud_alerts", "iocs",
           "regulatory", "rings")


def save(sim: SimulationResult, out_dir: Path | None = None) -> Path:
    """Persist every table. Parquet when available, CSV otherwise."""
    out = Path(out_dir or DATA_DIR)
    out.mkdir(parents=True, exist_ok=True)
    for name in _TABLES:
        df = getattr(sim, name)
        try:
            df.to_parquet(out / f"{name}.parquet", index=False)
        except Exception:  # pyarrow missing - stay usable anyway
            df.to_csv(out / f"{name}.csv", index=False)
    (out / "meta.json").write_text(json.dumps(sim.summary(), indent=2, default=str))
    return out


def load(in_dir: Path | None = None) -> SimulationResult:
    """Read back what :func:`save` wrote."""
    src = Path(in_dir or DATA_DIR)
    frames = {}
    for name in _TABLES:
        pq, csv = src / f"{name}.parquet", src / f"{name}.csv"
        if pq.exists():
            frames[name] = pd.read_parquet(pq)
        elif csv.exists():
            frames[name] = pd.read_csv(csv)
        else:
            raise FileNotFoundError(f"missing table {name!r} in {src} - run `make data`")
    for col, frame in (("timestamp", "transactions"), ("reported_at", "fraud_alerts"),
                       ("observed_at", "iocs"), ("published_at", "regulatory")):
        f = frames[frame]
        if col in f.columns and len(f):
            # ``format="ISO8601"`` rather than the default: the CSV fallback
            # round-trips nanosecond precision unevenly, so some rows carry a
            # fractional part and others do not.
            f[col] = pd.to_datetime(f[col], utc=True, format="ISO8601")
    meta_path = src / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return SimulationResult(meta=meta, **frames)


__all__ = ["SimulationResult", "generate", "save", "load", "CASH_POOL", "SIM_END"]
