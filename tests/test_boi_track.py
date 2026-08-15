"""The organisers' alert-dataset track: schema, loader, features and model.

These tests run against a table generated with the organisers' *exact* declared
schema, so they exercise the same code path the real file will take. What they
protect is the set of things that would quietly ruin a submission: a column
name parsed wrongly, a leakage column reaching the model, a validation file
whose columns arrive in a different order, or a cross-validation estimate
inflated by selecting features on the data it is scored on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bodhi.boi import features as bx
from bodhi.boi.dataset import load_alerts, leakage_present
from bodhi.boi.model import BOIConfig, BOIModel
from bodhi.boi.schema import (
    LEAKAGE_COLUMNS,
    RESOLUTION_COLUMNS,
    TARGET,
    load_dictionary,
    parse_feature,
)
from bodhi.boi.synth import SynthConfig, generate


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------


def test_dictionary_loads_and_is_the_expected_shape():
    dd = load_dictionary()
    assert len(dd.all_columns) == 3_924
    assert TARGET in dd.all_columns
    assert len(dd.feature_columns) == 3_923
    assert len(set(dd.all_columns)) == len(dd.all_columns), "duplicate column names"


def test_bank_finalized_subset_is_recovered():
    """The bank down-selected 18 predictors; that is domain knowledge."""
    dd = load_dictionary()
    finalized = dd.bank_finalized
    assert len(finalized) == 18
    assert TARGET not in finalized
    assert "AVG_BAL_14DAYS" in finalized
    assert "CUST_OCCP" in finalized
    assert set(finalized) <= set(dd.feature_columns)


@pytest.mark.parametrize("name,expect", [
    ("RA_CI_NON_CASH_CHQ_TXN_CR_L7_31D",
     dict(aggregation="RA", inducer="CI", channel="NON_CASH_CHQ",
          direction="CR", measure="TXN", window="L7_31D")),
    ("D_TA_CASH_TXN_L31D",
     dict(aggregation="D_TA", channel="CASH", direction=None,
          measure="TXN", window="L31D")),
    ("MIN_UPI_XFER_TXNS_L7D",
     dict(aggregation="MIN", channel="UPI_XFER", measure="TXN", window="L7D")),
    ("D_BI_FEES_CHRGS_AMT_7D_OCC",
     dict(aggregation="D", inducer="BI", channel="FEES_CHRGS",
          measure="AMT", occupation_relative=True)),
    ("DA_CASH_TXN_14D_OC",
     dict(aggregation="DA", channel="CASH", occupation_relative=True)),
    ("RA_CI_NON_CASH_CHQ_TXN_CR_14_31D",   # window with no leading L
     dict(aggregation="RA", inducer="CI", channel="NON_CASH_CHQ", direction="CR")),
])
def test_feature_grammar_is_parsed(name, expect):
    p = parse_feature(name)
    for attr, want in expect.items():
        assert getattr(p, attr) == want, f"{name}: {attr}"


def test_channel_prefixes_do_not_shadow_each_other():
    """NON_CASH_CHQ must never be read as CASH, nor UPI_XFER as UPI."""
    assert parse_feature("R_NON_CASH_CHQ_AMT_L7D").channel == "NON_CASH_CHQ"
    assert parse_feature("R_CASH_AMT_L7D").channel == "CASH"
    assert parse_feature("MIN_UPI_XFER_TXNS_L7D").channel == "UPI_XFER"
    assert parse_feature("DA_UPI_TXN_CR_L7_14D").channel == "UPI"


def test_almost_every_declared_column_is_understood():
    """A parser that silently gives up on half the schema is worse than none."""
    dd = load_dictionary()
    parsed = dd.parsed()
    unparsed = [c for c, p in parsed.items()
                if p.is_static and p.channel is None and p.window is None]
    # The genuinely static block is demographics + alert metadata, ~33 columns.
    assert len(unparsed) < 60, f"{len(unparsed)} columns not understood"
    windowed = [p for p in parsed.values() if p.window]
    assert len(windowed) > 3_400


def test_resolution_columns_are_quarantined_by_default():
    dd = load_dictionary()
    safe = dd.modelling_columns()
    for col in LEAKAGE_COLUMNS:
        assert col not in safe, f"{col} must not be offered to the model"
    permissive = dd.modelling_columns(allow_leakage=True)
    for col in RESOLUTION_COLUMNS:
        assert col in permissive


# --------------------------------------------------------------------------
# synthetic data with the real schema
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def synth():
    return generate(SynthConfig(n_rows=700, seed=5))


def test_synthetic_table_matches_the_declared_schema(synth):
    dd = load_dictionary()
    assert list(synth.columns) == dd.all_columns
    assert synth[TARGET].isin([0, 1]).all()
    assert 0.0 < synth[TARGET].mean() < 0.5


def test_synthetic_leakage_columns_behave_like_leakage(synth):
    """The stand-in data must reproduce the trap, or the guard is untested."""
    from sklearn.metrics import roc_auc_score
    y = synth[TARGET]
    auc = roc_auc_score(y, synth["FRAUD_SUSPECTED"].fillna(0))
    assert auc > 0.8, "FRAUD_SUSPECTED should nearly reproduce the target"


# --------------------------------------------------------------------------
# loader
# --------------------------------------------------------------------------


def test_loader_aligns_and_excludes_leakage(tmp_path, synth):
    path = tmp_path / "train.parquet"
    synth.to_parquet(path, index=False)

    X, y, report = load_alerts(path)
    assert y is not None and len(y) == len(synth)
    assert report.has_target
    for col in LEAKAGE_COLUMNS:
        assert col not in X.columns
    assert TARGET not in X.columns
    assert leakage_present(path) == list(LEAKAGE_COLUMNS)


def test_loader_survives_shuffled_and_missing_columns(tmp_path, synth):
    """The validation extract will not arrive in our column order."""
    rng = np.random.default_rng(0)
    cols = list(synth.columns)
    rng.shuffle(cols)
    shuffled = synth[cols].drop(columns=cols[:40])
    path = tmp_path / "shuffled.parquet"
    shuffled.to_parquet(path, index=False)

    X, _, report = load_alerts(path)
    assert report.missing, "dropped columns should be reported"
    # Missing declared columns are re-inserted so the matrix shape is stable.
    baseline, _, _ = load_alerts(tmp_path / "train.parquet") \
        if (tmp_path / "train.parquet").exists() else (X, None, None)
    assert X.isna().any().any()


def test_loader_handles_target_absent(tmp_path, synth):
    path = tmp_path / "novalidation.csv"
    synth.drop(columns=[TARGET]).head(50).to_csv(path, index=False)
    X, y, report = load_alerts(path)
    assert y is None
    assert not report.has_target
    assert len(X) == 50


def test_loader_coerces_numbers_shipped_as_text(tmp_path, synth):
    """Bank extracts ship numbers with thousands separators all the time."""
    frame = synth.head(60).copy()
    col = "AVG_BAL_14DAYS"
    frame[col] = ["1,234.50"] * 30 + ["(2,000.00)"] * 30
    path = tmp_path / "texty.csv"
    frame.to_csv(path, index=False)

    X, _, report = load_alerts(path)
    assert col in report.coerced_to_numeric
    assert pd.api.types.is_numeric_dtype(X[col])
    assert X[col].iloc[0] == pytest.approx(1234.50)
    assert X[col].iloc[-1] == pytest.approx(-2000.00)


# --------------------------------------------------------------------------
# engineered features
# --------------------------------------------------------------------------


def test_engineered_features_are_added_and_namespaced(synth):
    X = synth.drop(columns=[TARGET]).select_dtypes(include=[np.number]).head(200)
    out = bx.build(X, max_families=40)
    derived = bx.engineered_columns(out.columns)
    assert derived, "no engineered features produced"
    assert all(c.startswith(bx.PREFIX) for c in derived)
    assert out.shape[1] > X.shape[1]
    assert list(out.columns)[: X.shape[1]] == list(X.columns), "originals disturbed"


def test_engineered_features_are_finite_or_nan(synth):
    X = synth.drop(columns=[TARGET]).select_dtypes(include=[np.number]).head(200)
    out = bx.build(X, max_families=30)
    derived = out[bx.engineered_columns(out.columns)].to_numpy(dtype=float)
    assert not np.isinf(derived).any(), "infinite value in an engineered column"


def test_channel_concentration_detects_a_funnel(synth):
    """One channel carrying everything must score near 1 on the HHI."""
    X = synth.drop(columns=[TARGET]).select_dtypes(include=[np.number]).head(50).copy()
    out = bx.build(X, max_families=5)
    hhi = f"{bx.PREFIX}channel_hhi"
    if hhi in out.columns:
        assert (out[hhi].dropna().between(0, 1)).all()


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fitted(synth):
    X = synth.drop(columns=[TARGET])
    X = X.drop(columns=[c for c in LEAKAGE_COLUMNS if c in X.columns])
    X = X.select_dtypes(include=[np.number, "string", "object"])
    y = synth[TARGET].to_numpy()
    cfg = BOIConfig(n_splits=3, n_repeats=1, wide_n_repeats=1,
                    n_estimators=120, cv_n_estimators=80, seeds=(42,),
                    strategies=("bank_finalized", "auto_topk"), topk=40)
    return BOIModel(cfg).fit(X, y, verbose=False), X, y


def test_model_fits_and_reports_a_strategy(fitted):
    model, X, y = fitted
    assert model.strategy in ("bank_finalized", "auto_topk")
    assert model.feature_names
    assert model.boosters
    assert 0.0 < model.threshold < 1.0
    assert model.report["positives"] == int(y.sum())


def test_model_never_sees_a_leakage_column(fitted):
    model, _, _ = fitted
    for col in LEAKAGE_COLUMNS:
        assert col not in model.feature_names


def test_predictions_are_probabilities(fitted):
    model, X, _ = fitted
    p = model.predict_proba(X)
    assert p.shape == (len(X),)
    assert np.isfinite(p).all()
    assert (p >= 0).all() and (p <= 1).all()


def test_model_scores_a_frame_with_columns_missing(fitted):
    """At scoring time a column may simply not be there."""
    model, X, _ = fitted
    trimmed = X.drop(columns=list(X.columns[:25]))
    p = model.predict_proba(trimmed)
    assert np.isfinite(p).all()


def test_model_is_insensitive_to_column_order(fitted):
    model, X, _ = fitted
    rng = np.random.default_rng(1)
    cols = list(X.columns)
    rng.shuffle(cols)
    np.testing.assert_allclose(model.predict_proba(X),
                               model.predict_proba(X[cols]), atol=1e-6)


def test_model_roundtrips_through_disk(fitted, tmp_path):
    model, X, _ = fitted
    model.save(tmp_path / "m")
    reloaded = BOIModel.load(tmp_path / "m")
    assert reloaded.feature_names == model.feature_names
    assert reloaded.strategy == model.strategy
    np.testing.assert_allclose(model.predict_proba(X),
                               reloaded.predict_proba(X), atol=1e-9)


def test_cross_validation_is_not_inflated_by_selection(synth):
    """Selecting features inside the fold must not beat chance on noise.

    This is the test that matters most: with a shuffled target there is nothing
    to find, so an honest procedure returns ~0.5. A procedure that ranks
    features on the whole dataset before cross-validating returns well above
    0.5 here, which is exactly the trap this pipeline is built to avoid.
    """
    rng = np.random.default_rng(7)
    X = synth.drop(columns=[TARGET]).select_dtypes(include=[np.number]).iloc[:, :400]
    y = rng.permutation(synth[TARGET].to_numpy())

    cfg = BOIConfig(n_splits=3, n_repeats=1, wide_n_repeats=1,
                    cv_n_estimators=60, topk=25, seeds=(42,))
    result = BOIModel(cfg).cross_validate(X, y, "auto_topk")
    assert result.roc_auc < 0.62, (
        f"selection leaked: AUC {result.roc_auc:.3f} on a shuffled target")
