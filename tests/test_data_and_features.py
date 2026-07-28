"""The simulator and the feature layer must be internally consistent.

These are the tests that catch the failure mode nobody notices until the
results are already in a slide deck: a generator that leaks the label into a
feature, or a feature that quietly looks into the future.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bodhi.config import DataConfig
from bodhi.data.generator import CASH_POOL, generate
from bodhi.features import build_account_features, build_intel_features
from bodhi.features.engineering import BEHAVIOUR_FEATURES


def test_simulation_is_structurally_sound(sim):
    assert len(sim.accounts) == 1_200
    assert len(sim.transactions) > 1_000
    assert sim.accounts["is_mule"].sum() > 0
    assert sim.transactions["is_fraud"].sum() > 0
    # Fraud must be rare; a generator that makes it common proves nothing.
    assert sim.transactions["is_fraud"].mean() < 0.10
    assert sim.transactions["ts"].is_monotonic_increasing
    assert sim.transactions["amount"].min() > 0
    assert not sim.transactions["txn_id"].duplicated().any()


def test_every_typology_is_represented(sim):
    typologies = set(sim.rings["typology"])
    assert len(typologies) >= 4, f"only produced {typologies}"
    roles = set(sim.accounts.loc[sim.accounts["is_mule"], "mule_role"])
    assert {"COLLECTOR", "CASHOUT"} <= roles


def test_hard_negatives_are_planted(sim):
    """Without decoys the precision figures would be meaningless."""
    for col in ("is_small_business", "legit_dormant_wake",
                "is_legit_collector", "is_bc_agent"):
        assert sim.accounts[col].sum() > 0, f"no {col} population"
    # And they must not secretly be mules.
    decoys = sim.accounts[sim.accounts["is_bc_agent"]
                          | sim.accounts["is_legit_collector"]]
    assert not decoys["is_mule"].any()


def test_generation_is_reproducible():
    cfg = DataConfig(n_accounts=400, n_days=20, n_devices=300, n_merchants=25, seed=3)
    a, b = generate(cfg), generate(cfg)
    assert len(a.transactions) == len(b.transactions)
    assert a.accounts["is_mule"].sum() == b.accounts["is_mule"].sum()
    np.testing.assert_allclose(a.transactions["amount"].to_numpy(),
                               b.transactions["amount"].to_numpy())


def test_cash_pool_is_a_sink(sim):
    """Cash leaves the system; it must never originate a payment."""
    assert not (sim.transactions["src_account"] == CASH_POOL).any()
    cash = sim.transactions[sim.transactions["dst_account"] == CASH_POOL]
    assert set(cash["txn_type"]) <= {"CASH_WITHDRAWAL"}


# --------------------------------------------------------------------------


def test_feature_matrix_shape_and_hygiene(features, sim):
    assert list(features.columns) == list(BEHAVIOUR_FEATURES)
    assert len(features) == len(sim.accounts)
    assert np.isfinite(features.to_numpy()).all(), "non-finite feature emitted"
    assert not features.isna().any().any()


def test_features_never_see_the_label(sim):
    """Shuffling the labels must not change a single feature value."""
    shuffled = sim.accounts.copy()
    rng = np.random.default_rng(0)
    shuffled["is_mule"] = rng.permutation(shuffled["is_mule"].to_numpy())
    shuffled["mule_typology"] = rng.permutation(shuffled["mule_typology"].to_numpy())
    a = build_account_features(sim.transactions, sim.accounts)
    b = build_account_features(sim.transactions, shuffled)
    pd.testing.assert_frame_equal(a, b)


def test_as_of_truncation_is_monotone(sim):
    """A feature computed earlier can never know about later events."""
    ts = sim.transactions["ts"]
    early_cut = float(ts.quantile(0.5))
    early = build_account_features(sim.transactions, sim.accounts, as_of=early_cut)
    late = build_account_features(sim.transactions, sim.accounts)
    # Counts are cumulative, so they can only grow.
    assert (late["total_count"] >= early["total_count"] - 1e-9).all()
    assert (late["in_amount"] >= early["in_amount"] - 1e-6).all()
    assert (late["unique_payers"] >= early["unique_payers"] - 1e-9).all()


def test_as_of_matches_a_manually_truncated_ledger(sim):
    cut = float(sim.transactions["ts"].quantile(0.6))
    via_arg = build_account_features(sim.transactions, sim.accounts, as_of=cut)
    manual = build_account_features(
        sim.transactions[sim.transactions["ts"] <= cut], sim.accounts, as_of=cut)
    pd.testing.assert_frame_equal(via_arg, manual)


def test_passthrough_and_cashout_are_computed_correctly():
    """Hand-built ledger with an arithmetically known answer."""
    accounts = pd.DataFrame({
        "account_id": ["A", "B", "C"],
        "kyc_level": ["FULL"] * 3,
        "customer_age": [30, 30, 30],
        "account_vintage_days": [500] * 3,
        "is_merchant": [False] * 3,
        "vpa": ["a@x", "b@x", "c@x"],
    })
    base = 1_700_000_000.0
    tx = pd.DataFrame({
        "txn_id": ["T1", "T2", "T3"],
        "ts": [base, base + 600, base + 1_200],       # 10 and 20 minutes later
        "src_account": ["A", "B", "B"],
        "dst_account": ["B", "C", CASH_POOL],
        "amount": [10_000.0, 6_000.0, 3_000.0],
        "channel": ["UPI", "UPI", "ATM"],
        "txn_type": ["P2P", "P2P", "CASH_WITHDRAWAL"],
        "device_id": ["D1", "D2", None],
        "ip_address": ["1.1.1.1", "2.2.2.2", None],
        "beneficiary_age_hours": [0.5, 0.5, None],
    })
    f = build_account_features(tx, accounts)

    # B received 10,000 and sent 9,000 out, all within the hour.
    assert f.loc["B", "in_amount"] == pytest.approx(10_000.0)
    assert f.loc["B", "out_amount"] == pytest.approx(9_000.0)
    assert f.loc["B", "passthrough_ratio"] == pytest.approx(0.9, abs=1e-6)
    assert f.loc["B", "rapid_passthrough_ratio"] == pytest.approx(0.9, abs=1e-6)
    assert f.loc["B", "cashout_ratio"] == pytest.approx(0.3, abs=1e-6)
    # First debit follows the credit by ten minutes.
    assert f.loc["B", "min_in_to_out_min"] == pytest.approx(10.0, abs=1e-6)
    assert f.loc["B", "unique_payers"] == 1
    assert f.loc["B", "unique_payees"] == 1
    # A only pays out; C only receives.
    assert f.loc["A", "in_amount"] == 0
    assert f.loc["C", "out_amount"] == 0


def test_structuring_detector_uses_the_regulatory_band():
    accounts = pd.DataFrame({
        "account_id": ["A", "B"], "kyc_level": ["FULL"] * 2,
        "customer_age": [30, 30], "account_vintage_days": [500, 500],
        "is_merchant": [False, False], "vpa": ["a@x", "b@x"],
    })
    base = 1_700_000_000.0
    amounts = [46_000.0, 47_500.0, 49_000.0, 12_000.0]
    tx = pd.DataFrame({
        "txn_id": [f"T{i}" for i in range(4)],
        "ts": [base + i * 3_600 for i in range(4)],
        "src_account": ["A"] * 4, "dst_account": ["B"] * 4,
        "amount": amounts, "channel": ["IMPS"] * 4, "txn_type": ["P2P"] * 4,
        "device_id": ["D"] * 4, "ip_address": ["1.1.1.1"] * 4,
        "beneficiary_age_hours": [100.0] * 4,
    })
    f = build_account_features(tx, accounts)
    assert f.loc["A", "structuring_frac"] == pytest.approx(0.75)


def test_intel_features_respect_report_time(sim):
    """A ticket filed tomorrow must be invisible today."""
    if not len(sim.fraud_alerts):
        pytest.skip("no tickets in this simulation")
    reported = pd.to_datetime(sim.fraud_alerts["reported_at"], utc=True)
    cut = float(reported.astype("int64").quantile(0.25) / 1e9)
    early = build_intel_features(sim.accounts, sim.fraud_alerts, sim.iocs,
                                 sim.regulatory, as_of=cut)
    late = build_intel_features(sim.accounts, sim.fraud_alerts, sim.iocs,
                                sim.regulatory)
    assert early["ncrp_ticket_count"].sum() < late["ncrp_ticket_count"].sum()
    assert (late["ncrp_ticket_count"] >= early["ncrp_ticket_count"]).all()


def test_intel_is_not_part_of_the_supervised_feature_set():
    """The claim 'we catch mules nobody reported' depends on this."""
    from bodhi.features.engineering import INTEL_FEATURES
    assert not (set(INTEL_FEATURES) & set(BEHAVIOUR_FEATURES))


def test_mules_separate_from_legitimate_accounts(features, sim):
    """A sanity floor: if these do not separate, nothing downstream can work."""
    y = sim.accounts.set_index("account_id")["is_mule"].reindex(features.index)
    mule = features[y.to_numpy()]
    clean = features[~y.to_numpy()]
    assert mule["rapid_passthrough_ratio"].mean() > clean["rapid_passthrough_ratio"].mean()
    assert mule["new_beneficiary_frac"].mean() > clean["new_beneficiary_frac"].mean()
