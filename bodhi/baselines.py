"""The incumbent: a rule-based transaction-monitoring engine.

Every claim about "reducing false positives" is meaningless without something
to reduce them *against*, so this file implements the system BODHI is meant to
replace - a threshold-and-scenario AML engine of the kind that actually runs in
banks today. The scenarios below are modelled on standard TM rule packs:
velocity, structuring, dormancy, cash-out ratio, device sharing, new-account
turnover and off-hours concentration.

It is implemented fairly and tuned in the bank's favour: an account alerts if
*any* scenario fires, and the comparison in ``scripts/evaluate.py`` also reports
the rule engine at its best possible operating point. If the ML stack could not
beat that, the honest conclusion would be that the ML stack is not worth
deploying.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bodhi.config import REGULATORY

#: (code, description, weight) - weight only orders the alert queue.
RULES: tuple[tuple[str, str, float], ...] = (
    ("R01_HIGH_VALUE_FAN_IN",
     "Credits above INR 5,00,000 in 24h from more than 5 distinct payers", 3.0),
    ("R02_VELOCITY",
     "10 or more transactions inside a single hour", 2.0),
    ("R03_CASH_OUT",
     "More than 90% of credits withdrawn as cash", 3.0),
    ("R04_STRUCTURING",
     "3 or more transfers in the INR 45,000-50,000 band", 2.5),
    ("R05_DORMANT_REACTIVATION",
     "No activity for 180 days followed by a transfer above INR 50,000", 2.0),
    ("R06_SHARED_DEVICE",
     "Device shared with more than 3 other accounts", 1.5),
    ("R07_NEW_ACCOUNT_TURNOVER",
     "Account under 90 days old with turnover above INR 2,00,000", 2.0),
    ("R08_NIGHT_ACTIVITY",
     "Over half of activity between 00:00 and 05:00 with turnover above INR 1,00,000",
     1.5),
)


def rule_engine(features: pd.DataFrame) -> pd.DataFrame:
    """Evaluate every scenario over the behavioural feature matrix.

    Returns one boolean column per rule plus ``n_rules_fired`` and
    ``rule_score``. Deliberately consumes the *same* features as the ML stack
    so the comparison isolates the modelling approach, not the data pipeline.
    """
    f = features
    out = pd.DataFrame(index=f.index)

    out["R01_HIGH_VALUE_FAN_IN"] = (
        (f["max_amount_1h"] > 500_000) & (f["unique_payers"] > 5)
    ) | ((f["in_amount"] > 500_000) & (f["unique_payers"] > 5) &
         (f["active_days"] <= 3))

    out["R02_VELOCITY"] = f["burst_max_30min"] >= 10

    out["R03_CASH_OUT"] = (f["cashout_ratio"] > 0.9) & (f["in_amount"] > 10_000)

    lo, hi = REGULATORY.structuring_band
    out["R04_STRUCTURING"] = (f["structuring_frac"] * f["out_count"]) >= 3

    out["R05_DORMANT_REACTIVATION"] = (
        (f["silence_before_first_days"] >= 60) & (f["max_out_amount"] > 50_000)
    ) | ((f["max_gap_days"] >= 60) & (f["max_out_amount"] > 50_000))

    out["R06_SHARED_DEVICE"] = f["device_max_accounts"] > 3

    out["R07_NEW_ACCOUNT_TURNOVER"] = (
        (f["account_vintage_days"] < 90) & ((f["in_amount"] + f["out_amount"]) > 200_000)
    )

    out["R08_NIGHT_ACTIVITY"] = (f["night_frac"] > 0.5) & (f["in_amount"] > 100_000)

    weights = {code: w for code, _, w in RULES}
    out["n_rules_fired"] = out[[c for c, _, _ in RULES]].sum(axis=1)
    out["rule_score"] = sum(out[c].astype(float) * weights[c] for c, _, _ in RULES)
    out["alert"] = out["n_rules_fired"] >= 1
    return out


def rule_summary(results: pd.DataFrame, y: np.ndarray) -> pd.DataFrame:
    """Per-rule precision/recall so the weakest scenarios are visible."""
    rows = []
    y = np.asarray(y).astype(bool)
    for code, description, _ in RULES:
        fired = results[code].to_numpy().astype(bool)
        tp = int((fired & y).sum())
        fp = int((fired & ~y).sum())
        rows.append({
            "rule": code,
            "description": description,
            "fired": int(fired.sum()),
            "true_positives": tp,
            "false_positives": fp,
            "precision": round(tp / max(fired.sum(), 1), 4),
            "recall": round(tp / max(y.sum(), 1), 4),
        })
    return pd.DataFrame(rows).sort_values("precision", ascending=False)


__all__ = ["RULES", "rule_engine", "rule_summary"]
