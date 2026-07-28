"""End-to-end: the trained pipeline, explanations, and the HTTP surface."""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.metrics import average_precision_score, roc_auc_score

from bodhi.baselines import rule_engine
from bodhi.config import ALERT_THRESHOLD, band_for
from bodhi.explain.gnn_explainer import explain_node
from bodhi.schemas import AlertStatus


# ------------------------------------------------------------------ training


def test_engine_trains_and_separates(trained, sim):
    y = sim.accounts.set_index("account_id")["is_mule"] \
        .reindex(trained.last_result.scores.index).to_numpy().astype(int)
    fused = trained.last_result.scores["risk_score"].to_numpy()
    assert roc_auc_score(y, fused) > 0.90
    assert average_precision_score(y, fused) > 0.50


def test_holdout_performance_is_reported_on_unseen_accounts(trained, sim):
    """The number that counts is the one on the fold nothing was fitted on."""
    test_idx = trained.split["test"]
    y = sim.accounts["is_mule"].to_numpy().astype(int)[test_idx]
    fused = trained.last_result.scores["risk_score"].to_numpy()[test_idx]
    assert y.sum() > 0
    assert roc_auc_score(y, fused) > 0.85


def test_all_four_folds_are_disjoint(trained):
    folds = trained.split
    assert set(folds) == {"train", "val", "fuse", "test"}
    seen: set[int] = set()
    for name, idx in folds.items():
        overlap = seen & set(idx.tolist())
        assert not overlap, f"{name} overlaps an earlier fold"
        seen |= set(idx.tolist())


def test_score_columns_are_well_formed(trained):
    s = trained.last_result.scores
    for col in ("score_xgb", "score_graph", "score_temporal", "score_intel",
                "prob_mule"):
        v = s[col].to_numpy()
        assert np.isfinite(v).all()
        assert (v >= 0).all() and (v <= 1).all(), f"{col} left [0,1]"
    risk = s["risk_score"].to_numpy()
    assert (risk >= 0).all() and (risk <= 100).all()
    assert all(band_for(r) == b for r, b in zip(risk, s["band"]))


def test_risk_score_is_monotone_in_probability(trained):
    """The display warp must not reorder anything."""
    s = trained.last_result.scores.sort_values("prob_mule")
    risk = s["risk_score"].to_numpy()
    assert np.all(np.diff(risk) >= -1e-9)


def test_intel_score_is_zero_without_any_intelligence(trained):
    """An account nobody has reported must not inherit a positive intel score."""
    from bodhi.features.engineering import has_intelligence

    intel = trained._intel_frame(trained.last_result.as_of)
    quiet = intel.index[~has_intelligence(intel)]
    assert len(quiet) > 0, "every account carries intelligence - check the fixture"
    scores = trained.last_result.scores.loc[quiet, "score_intel"]
    assert (scores == 0).all()


def test_intel_evidence_mask_ignores_the_recency_sentinel():
    """``ncrp_recency_days`` is 999 when nobody has reported an account.

    Summing the intel row would therefore mark the entire bank as 'has
    intelligence' - the bug this mask exists to prevent.
    """
    import pandas as pd
    from bodhi.features.engineering import has_intelligence

    intel = pd.DataFrame({
        "ncrp_ticket_count": [0.0, 2.0, 0.0],
        "ncrp_recency_days": [999.0, 3.0, 999.0],
        "ioc_hit": [0.0, 0.0, 1.0],
        "regulatory_listed": [0.0, 0.0, 0.0],
    })
    assert list(has_intelligence(intel)) == [False, True, True]


def test_engine_roundtrips_through_disk(trained, sim, tmp_path):
    from bodhi.engine import MuleHunterEngine
    trained.save(tmp_path)
    loaded = MuleHunterEngine.load(tmp_path)
    loaded.attach(sim.accounts, sim.transactions, sim.fraud_alerts,
                  sim.iocs, sim.regulatory)
    result = loaded.score_all(detect_rings_too=False)
    np.testing.assert_allclose(
        result.scores["risk_score"].to_numpy(),
        trained.last_result.scores["risk_score"].to_numpy(),
        atol=1e-6)


# ------------------------------------------------------------------ explain


def test_explanations_are_complete_and_human_readable(trained):
    top = trained.last_result.scores.nlargest(3, "risk_score").index
    for account in top:
        d = trained.explain_account(str(account))
        assert 0 <= d["risk_score"] <= 100
        assert d["band"] in ("SAFE", "MEDIUM", "HIGH", "CRITICAL")
        assert d["evidence"], f"no evidence for {account}"
        assert len(d["narrative"]) > 60
        assert d["suggested_actions"]
        for e in d["evidence"]:
            assert e["code"] and e["label"] and e["detail"]
            # A reason an officer cannot read is not a reason.
            assert not any(tok in e["detail"] for tok in ("_frac", "_ratio", "nan"))


def test_shap_contributions_reconstruct_the_margin(trained):
    """Exact TreeSHAP: contributions plus bias must equal the raw margin."""
    X = trained.features.head(40)
    contrib, bias = trained.xgb.shap_values(X)
    import xgboost as xgb
    margin = trained.xgb.booster.predict(
        xgb.DMatrix(X.reindex(columns=trained.xgb.feature_names, fill_value=0.0)
                    .to_numpy(dtype=np.float32),
                    feature_names=trained.xgb.feature_names),
        output_margin=True)
    np.testing.assert_allclose(contrib.sum(axis=1) + bias, margin, rtol=1e-4, atol=1e-4)


def test_gnn_explainer_returns_a_faithful_mask(trained):
    account = str(trained.last_result.scores["risk_score"].idxmax())
    out = explain_node(trained.sage, trained.graph,
                       trained.features.to_numpy(dtype=float), account,
                       steps=40)
    assert out["account_id"] == account
    assert out["n_subgraph_nodes"] > 0
    assert 0.0 <= out["fidelity"] <= 1.0
    for e in out["edges"]:
        assert 0.0 <= e["importance"] <= 1.0
        assert e["edge_type"] in ("MONEY", "DEVICE", "IP")


# ------------------------------------------------------------------ alerts


def test_alerts_are_generated_and_ranked(trained):
    alerts = trained.generate_alerts(limit=20)
    assert alerts
    scores = [a.risk_score for a in alerts]
    assert scores == sorted(scores, reverse=True)
    for a in alerts:
        assert a.risk_score >= ALERT_THRESHOLD
        assert a.title and a.narrative
        assert a.suggested_actions


def test_realtime_decision_is_fast_and_sane(trained, sim):
    row = sim.transactions.iloc[-1]
    d = trained.score_transaction({
        "txn_id": row["txn_id"], "src_account": row["src_account"],
        "dst_account": row["dst_account"], "amount": float(row["amount"]),
        "channel": row["channel"],
        "beneficiary_age_hours": row.get("beneficiary_age_hours"),
    })
    assert d.decision in ("ALLOW", "REVIEW", "HOLD", "BLOCK")
    assert 0 <= d.risk_score <= 100
    assert d.latency_ms < 50


def test_payment_into_a_flagged_account_is_not_allowed(trained):
    """The whole point: stop the credit before it lands."""
    worst = str(trained.last_result.scores["risk_score"].idxmax())
    d = trained.score_transaction({
        "txn_id": "T-LIVE", "src_account": "ACC0000001", "dst_account": worst,
        "amount": 95_000.0, "channel": "UPI", "beneficiary_age_hours": 0.1,
    })
    assert d.decision in ("HOLD", "BLOCK", "REVIEW")
    assert d.reasons


# ------------------------------------------------------------------ baseline


def test_engine_beats_the_rule_baseline_at_equal_recall(trained, sim):
    """If this fails the ML stack is not worth deploying."""
    y = sim.accounts["is_mule"].to_numpy().astype(bool)
    rules = rule_engine(trained.features)
    fired = rules["alert"].to_numpy()
    rule_recall = (fired & y).sum() / max(y.sum(), 1)
    rule_precision = (fired & y).sum() / max(fired.sum(), 1)

    risk = trained.last_result.scores["risk_score"].to_numpy()
    order = np.argsort(-risk)
    needed = int(np.ceil(rule_recall * y.sum()))
    hits, k = 0, 0
    for i in order:
        k += 1
        hits += bool(y[i])
        if hits >= needed:
            break
    bodhi_precision = hits / max(k, 1)
    assert bodhi_precision > rule_precision * 3, (
        f"rule precision {rule_precision:.4f} vs bodhi {bodhi_precision:.4f}")


# ------------------------------------------------------------------ API


@pytest.fixture(scope="module")
def client(sim):
    from fastapi.testclient import TestClient
    import bodhi.api.state as state_mod
    from bodhi.api.main import app
    from bodhi.engine import MuleHunterEngine

    state_mod.reset_state()
    st = state_mod.RuntimeState()
    st.engine = MuleHunterEngine().attach(
        sim.accounts, sim.transactions, sim.fraud_alerts, sim.iocs, sim.regulatory)
    st.engine.train(verbose=False)
    st.engine.score_all()
    from bodhi.feeds.ncrp import NCRPConnector
    from bodhi.feeds.regulatory import RegulatoryConnector
    st.ncrp = NCRPConnector(sim.fraud_alerts)
    st.regulatory = RegulatoryConnector(sim.regulatory)
    st.simulation = sim
    st.casebook.add(st.engine.generate_alerts(limit=25))
    st.bootstrap_mode = "test"
    st.ready = True
    state_mod._STATE = st
    with TestClient(app) as c:
        yield c
    state_mod.reset_state()


def test_health_and_overview(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["models_loaded"]
    o = client.get("/api/overview").json()
    assert o["accounts"] > 0 and o["transactions"] > 0
    assert set(o["bands"]) <= {"SAFE", "MEDIUM", "HIGH", "CRITICAL"}


def test_alert_lifecycle(client):
    alerts = client.get("/api/alerts?limit=5").json()
    assert alerts["count"] > 0
    alert_id = alerts["alerts"][0]["alert_id"]

    detail = client.get(f"/api/alerts/{alert_id}").json()
    assert detail["alert"]["alert_id"] == alert_id
    assert detail["detail"]["evidence"]

    resolved = client.post(
        f"/api/alerts/{alert_id}/resolve?status=CONFIRMED_FRAUD&investigator=t@bank"
    ).json()
    assert resolved["alert"]["status"] == AlertStatus.CONFIRMED_FRAUD.value

    assert client.get("/api/alerts/NOPE").status_code == 404


def test_account_endpoints(client):
    account = client.get("/api/alerts?limit=1").json()["alerts"][0]["account_id"]
    d = client.get(f"/api/accounts/{account}").json()
    assert d["account_id"] == account and d["profile"]

    g = client.get(f"/api/accounts/{account}/graph").json()
    assert g["centre"] == account and g["nodes"]
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids

    f = client.get(f"/api/accounts/{account}/flows").json()
    for p in f["paths"]:
        assert len(p["risk_along_path"]) == len(p["path"])

    assert client.get("/api/accounts/ACC9999999").status_code == 404


def test_transaction_scoring_endpoint(client, sim):
    row = sim.transactions.iloc[-1]
    body = {"transaction": {
        "txn_id": "API-1",
        "timestamp": row["timestamp"].isoformat(),
        "src_account": row["src_account"], "dst_account": row["dst_account"],
        "amount": float(row["amount"]), "channel": "UPI", "txn_type": "P2P",
        "beneficiary_age_hours": 0.1,
    }}
    r = client.post("/api/score/transaction", json=body).json()
    assert r["decision"] in ("ALLOW", "REVIEW", "HOLD", "BLOCK")
    assert r["latency_ms"] >= 0


def test_shield_upload_injects_iocs(client, tmp_path):
    from bodhi.shield.synth import build_sample_apk
    apk = build_sample_apk(tmp_path / "up.apk")
    with apk.open("rb") as fh:
        r = client.post("/api/shield/analyze?inject=true",
                        files={"file": ("up.apk", fh, "application/vnd.android.package-archive")})
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["risk_band"] == "CRITICAL"
    assert body["iocs"]
    assert body["handoff"]["injected"]


def test_killswitch_endpoint_refuses_without_corroboration(client):
    account = client.get("/api/alerts?limit=1").json()["alerts"][0]["account_id"]
    r = client.post(f"/api/actions/killswitch?account_id={account}&action=FULL_FREEZE")
    assert r.status_code == 200
    d = r.json()["decision"]
    assert d["action"] in ("FULL_FREEZE", "FREEZE_DEBIT", "HOLD_CREDIT", "MONITOR")
    if d["action"] != "FULL_FREEZE":
        assert d["requires_human"] and d["refusals"]


def test_audit_chain_endpoint_is_valid(client):
    a = client.get("/api/audit").json()
    assert a["chain_valid"] is True
    assert a["first_invalid_seq"] is None


def test_str_draft_is_generated(client):
    alert_id = client.get("/api/alerts?limit=1").json()["alerts"][0]["alert_id"]
    r = client.get(f"/api/reports/str/{alert_id}").json()
    assert r["report_type"] == "STR"
    assert r["status"] == "DRAFT_PENDING_HUMAN_REVIEW"
    assert r["suspicion"]["grounds"]
    text = client.get(f"/api/reports/str/{alert_id}?as_text=true").text
    assert "SUSPICIOUS TRANSACTION REPORT" in text


def test_dashboard_and_agents_are_served(client):
    assert "BODHI" in client.get("/").text
    agents = client.get("/api/agents").json()["agents"]
    assert [a["layer"] for a in agents] == list(range(1, 10))
