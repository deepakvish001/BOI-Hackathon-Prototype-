"""APK triage, the IoC hand-off, PII handling, the audit chain and the
kill-switch's refusal logic."""

from __future__ import annotations

import struct
import zipfile

import pandas as pd
import pytest

from bodhi.actions.killswitch import KillSwitch
from bodhi.compliance.audit import AuditLog
from bodhi.compliance.pii import redact_frame, redact_text, tokenise
from bodhi.compliance.reports import build_ctr_candidates
from bodhi.config import KILLSWITCH_THRESHOLD
from bodhi.schemas import ActionType
from bodhi.shield import iocs_from_report, triage_apk
from bodhi.shield.apk_triage import parse_axml_strings
from bodhi.shield.ioc_bridge import ingest_iocs
from bodhi.shield.synth import build_axml, build_benign_apk, build_sample_apk


# ------------------------------------------------------------------ AXML


def test_axml_string_pool_roundtrips():
    """The parser must read back exactly what the real binary layout encodes."""
    strings = ["com.example.app", "android.permission.READ_SMS", "activity",
               "a", "", "ünïcødé", "x" * 300]
    parsed = parse_axml_strings(build_axml(strings))
    assert parsed == strings


def test_axml_parser_survives_garbage():
    assert parse_axml_strings(b"") == []
    assert parse_axml_strings(b"\x00" * 64) == []
    # A truncated but structurally plausible chunk must not raise.
    header = struct.pack("<HHI", 0x0003, 8, 10_000)
    assert parse_axml_strings(header + b"\x01\x00\x1c\x00" + b"\x00" * 20) == []


# ------------------------------------------------------------------ triage


@pytest.fixture(scope="module")
def trojan(tmp_path_factory):
    return triage_apk(build_sample_apk(tmp_path_factory.mktemp("apk") / "t.apk"))


@pytest.fixture(scope="module")
def benign(tmp_path_factory):
    return triage_apk(build_benign_apk(tmp_path_factory.mktemp("apk") / "b.apk"))


def test_triage_discriminates(trojan, benign):
    assert trojan.risk_band == "CRITICAL"
    assert benign.risk_band == "SAFE"
    assert trojan.risk_score > benign.risk_score + 50


def test_triage_extracts_the_financial_indicators(trojan):
    assert "collect9821@ybl" in trojan.upi_vpas
    assert "HDFC0001234" in trojan.ifsc_codes
    assert "103.145.22.71" in trojan.ip_addresses
    assert trojan.financial_indicators >= 5
    assert trojan.package_name == "com.secure.bankassist"


def test_triage_flags_toxic_combinations_not_single_permissions(trojan, benign):
    assert trojan.toxic_combinations
    assert not benign.toxic_combinations
    # INTERNET on its own carries no weight.
    assert all(p["permission"] != "android.permission.INTERNET"
               for p in benign.dangerous_permissions)


def test_triage_detects_packing_and_obfuscation(trojan, benign):
    assert trojan.packers
    assert trojan.obfuscation_ratio > 0.5
    assert not benign.packers
    assert benign.obfuscation_ratio < 0.25


def test_email_addresses_are_not_mistaken_for_upi_handles(tmp_path):
    apk = build_sample_apk(tmp_path / "e.apk",
                           vpas=("support@gmail", "real123@okaxis"))
    report = triage_apk(apk)
    assert "real123@okaxis" in report.upi_vpas
    assert not any(v.endswith("@gmail") for v in report.upi_vpas)


def test_non_apk_input_is_rejected_gracefully(tmp_path):
    p = tmp_path / "not.apk"
    p.write_bytes(b"this is definitely not a zip file")
    report = triage_apk(p)
    assert not report.is_valid_apk
    assert report.errors
    assert report.risk_band == "UNKNOWN"


def test_zip_without_manifest_is_flagged(tmp_path):
    p = tmp_path / "empty.apk"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("readme.txt", "hello")
    report = triage_apk(p)
    assert not report.is_valid_apk


# ------------------------------------------------------------------ IoCs


def test_ioc_confidence_reflects_the_carrier(trojan, benign):
    hot = {i.value: i.confidence for i in iocs_from_report(trojan)}
    cold = {i.value: i.confidence for i in iocs_from_report(benign)}
    assert max(hot.values()) > max(cold.values())
    assert all(0 <= c <= 1 for c in list(hot.values()) + list(cold.values()))


def test_ioc_ingest_deduplicates_keeping_best_confidence(trojan):
    iocs = iocs_from_report(trojan)
    first = ingest_iocs(iocs)
    second = ingest_iocs(iocs, first)
    assert len(second) == len(first)
    assert not second.duplicated(subset=["ioc_type", "value"]).any()


# ------------------------------------------------------------------ PII


def test_redaction_removes_every_identifier_class():
    text = ("Account ACC0001234 (50100234567891) VPA rahul.k@okaxis IFSC HDFC0001234 "
            "PAN ABCDE1234F phone +91 9876543210 mail a.b@example.com "
            "card 4111 1111 1111 1111 from 103.145.22.71")
    out = redact_text(text)
    for leaked in ("50100234567891", "rahul.k@okaxis", "HDFC0001234", "ABCDE1234F",
                   "9876543210", "4111", "103.145.22.71"):
        assert leaked not in out, f"{leaked} survived redaction"
    assert "[ACCOUNT]" in out and "[IFSC]" in out


def test_tokenisation_is_deterministic_and_keyed():
    assert tokenise("ACC0000001") == tokenise("ACC0000001")
    assert tokenise("ACC0000001") != tokenise("ACC0000002")
    assert tokenise("ACC0000001", salt="a") != tokenise("ACC0000001", salt="b")
    assert "ACC0000001" not in tokenise("ACC0000001")


def test_redact_frame_tokenises_and_drops(sim):
    sub = sim.transactions.head(50)
    tok = redact_frame(sub)
    assert (tok["src_account"] != sub["src_account"]).all()
    assert tok["src_account"].nunique() == sub["src_account"].nunique()
    dropped = redact_frame(sub, mode="drop")
    assert "src_account" not in dropped.columns


# ------------------------------------------------------------------ audit


def test_audit_chain_verifies_and_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(6):
        log.append("SYSTEM", "EVENT", {"i": i})
    ok, bad = log.verify()
    assert ok and bad is None
    assert len(log) == 6

    # Silently rewrite history.
    log.entries[3].payload_digest = "0" * 64
    ok, bad = log.verify()
    assert not ok and bad == 3


def test_audit_chain_persists_across_reloads(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("A", "ONE", {"x": 1})
    log.append("B", "TWO", {"x": 2})
    head = log.head
    reloaded = AuditLog(path)
    assert len(reloaded) == 2
    assert reloaded.head == head
    assert reloaded.verify()[0]


# ------------------------------------------------------------------ kill-switch


def _layers(**kw):
    base = {"xgb": 0.0, "graph": 0.0, "temporal": 0.0, "intel": 0.0}
    base.update(kw)
    return base


def test_killswitch_refuses_a_freeze_below_the_floor():
    ks = KillSwitch()
    d = ks.evaluate("ACC1", risk_score=70.0, proposed=ActionType.FULL_FREEZE,
                    layer_scores=_layers(xgb=0.9, graph=0.9))
    assert d.action != ActionType.FULL_FREEZE
    assert d.requires_human
    assert any("below the automation floor" in r for r in d.refusals)


def test_killswitch_requires_two_corroborating_layers():
    ks = KillSwitch()
    d = ks.evaluate("ACC2", risk_score=95.0, proposed=ActionType.FULL_FREEZE,
                    layer_scores=_layers(xgb=0.99))
    assert d.action != ActionType.FULL_FREEZE
    assert any("corroborate" in r for r in d.refusals)


def test_killswitch_applies_when_fully_corroborated():
    ks = KillSwitch()
    d = ks.evaluate("ACC3", risk_score=KILLSWITCH_THRESHOLD + 5,
                    proposed=ActionType.FULL_FREEZE,
                    layer_scores=_layers(xgb=0.95, graph=0.9, temporal=0.8))
    assert d.applied and d.action == ActionType.FULL_FREEZE
    assert d.expires_at is not None
    assert ks.status("ACC3")["action"] == "FULL_FREEZE"


def test_killswitch_protects_salary_accounts():
    ks = KillSwitch(protected_accounts={"ACC_SALARY"})
    d = ks.evaluate("ACC_SALARY", risk_score=99.0, proposed=ActionType.FULL_FREEZE,
                    layer_scores=_layers(xgb=0.99, graph=0.99, temporal=0.99))
    assert d.action != ActionType.FULL_FREEZE
    assert any("salary/pension" in r for r in d.refusals)


def test_killswitch_rate_limits_automated_freezes():
    ks = KillSwitch(max_auto_freezes_per_hour=2)
    layers = _layers(xgb=0.95, graph=0.95, temporal=0.9)
    applied = [ks.evaluate(f"A{i}", 95.0, ActionType.FULL_FREEZE, layers).action
               for i in range(5)]
    assert applied.count(ActionType.FULL_FREEZE) == 2
    assert ks.summary()["freezes_last_hour"] == 2


def test_killswitch_reversal_is_audited():
    ks = KillSwitch()
    ks.evaluate("ACC9", 95.0, ActionType.FULL_FREEZE,
                _layers(xgb=0.95, graph=0.9, temporal=0.9))
    before = len(ks.audit)
    assert ks.revert("ACC9")
    assert len(ks.audit) == before + 1
    assert ks.status("ACC9") is None
    assert not ks.revert("ACC9")


def test_every_killswitch_decision_is_audited():
    ks = KillSwitch()
    ks.evaluate("R1", 70.0, ActionType.FULL_FREEZE, _layers(xgb=0.9))  # refused
    ks.evaluate("R2", 95.0, ActionType.FULL_FREEZE,
                _layers(xgb=0.95, graph=0.95))                          # applied
    events = [e.event for e in ks.audit.entries]
    assert "KILLSWITCH_DOWNGRADE" in events
    assert "KILLSWITCH_APPLY" in events
    assert ks.audit.verify()[0]


# ------------------------------------------------------------------ CTR


def test_ctr_aggregates_per_account_day():
    """Structuring defeats a per-transaction check, so the rule aggregates."""
    midnight = pd.Timestamp("2026-05-04T00:00:00Z").timestamp()
    tx = pd.DataFrame({
        "txn_id": [f"T{i}" for i in range(5)],
        "ts": [midnight + 3_600 + i * 3_600 for i in range(5)],
        "src_account": ["A"] * 5,
        "dst_account": ["CASH_POOL"] * 5,
        "amount": [250_000.0] * 5,          # each below the 10 lakh threshold
        "channel": ["ATM"] * 5,
        "txn_type": ["CASH_WITHDRAWAL"] * 5,
    })
    out = build_ctr_candidates(tx)
    assert len(out) == 1
    assert out.iloc[0]["cash_total"] == pytest.approx(1_250_000.0)
    assert out.iloc[0]["n_transactions"] == 5


def test_ctr_does_not_aggregate_across_calendar_days():
    """The obligation is per day, so a run that straddles midnight splits."""
    midnight = pd.Timestamp("2026-05-04T00:00:00Z").timestamp()
    tx = pd.DataFrame({
        "txn_id": ["T0", "T1"],
        "ts": [midnight - 3_600, midnight + 3_600],
        "src_account": ["A", "A"],
        "dst_account": ["CASH_POOL"] * 2,
        "amount": [600_000.0, 600_000.0],
        "channel": ["ATM"] * 2,
        "txn_type": ["CASH_WITHDRAWAL"] * 2,
    })
    assert len(build_ctr_candidates(tx)) == 0
