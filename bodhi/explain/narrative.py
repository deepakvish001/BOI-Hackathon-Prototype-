"""Turn model internals into something an investigator can act on.

An alert that says ``rapid_passthrough_ratio = 0.94, SHAP +1.31`` is useless to
the officer who has to phone the customer. This module maps every feature onto
a sentence in the vocabulary of an AML desk, attaches the evidence that a
Suspicious Transaction Report will need, and proposes a proportionate action.

The catalogue is deliberately data, not code: adding a typology means adding a
row, and the wording that reaches a regulator stays reviewable in one place.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from bodhi.config import ALERT_THRESHOLD, KILLSWITCH_THRESHOLD, REGULATORY, band_for
from bodhi.schemas import ActionType, Evidence

#: feature -> (code, short label, sentence template, higher-is-worse)
REASON_CATALOG: dict[str, tuple[str, str, str, bool]] = {
    "rapid_passthrough_ratio": (
        "PASS_THROUGH", "Funds do not rest",
        "{pct} of every rupee credited left the account within "
        f"{60} minutes - the account is a conduit, not a destination.", True),
    "passthrough_ratio": (
        "FULL_DRAIN", "Outflow matches inflow",
        "Outflow equals {ratio}x inflow, leaving effectively nil balance.", True),
    "cashout_ratio": (
        "CASH_OUT", "Converted to cash",
        "{pct} of credits were withdrawn as cash through ATM/AePS, "
        "removing the funds from the banking system.", True),
    "unique_payers": (
        "FAN_IN", "Many unrelated payers",
        "{value:.0f} distinct counterparties credited this account with no "
        "prior relationship to any of them.", True),
    "fan_in_ratio": (
        "FAN_IN_RATIO", "One payment per payer",
        "Nearly every credit came from a different payer "
        "({ratio} unique payers per credit) - the signature of collection, "
        "not commerce.", True),
    "burst_max_30min": (
        "VELOCITY_BURST", "Transaction burst",
        "{value:.0f} transactions occurred inside a single 30-minute window.", True),
    "structuring_frac": (
        "STRUCTURING", "Sub-threshold structuring",
        "{pct} of outgoing transfers sat in the "
        f"INR {int(REGULATORY.structuring_band[0]):,}-"
        f"{int(REGULATORY.structuring_band[1]):,} band, just under the "
        "reporting boundary.", True),
    "dormancy_burst_score": (
        "DORMANT_BURST", "Dormant account reactivated",
        "The account was silent for {silence:.0f} days and then transacted at "
        "high velocity - consistent with a rented or purchased account.", True),
    "silence_before_first_days": (
        "LONG_DORMANCY", "Long dormancy",
        "No activity for the first {value:.0f} days of the observation window.", True),
    "device_max_accounts": (
        "DEVICE_FARM", "Shared handset",
        "A device used by this account also operates {value:.0f} other "
        "accounts - an implausible level of sharing for a retail customer.", True),
    "ip_max_accounts": (
        "SHARED_IP", "Shared network address",
        "{value:.0f} accounts transacted from the same public IP address.", True),
    "new_beneficiary_frac": (
        "FRESH_BENEFICIARY", "Payments to brand-new payees",
        "{pct} of debits went to beneficiaries added less than "
        f"{REGULATORY.fresh_beneficiary_hours} hours earlier.", True),
    "first_time_payer_frac": (
        "FIRST_TIME_PAYERS", "Credited by first-time payers",
        "{pct} of credits came from payers who had added this account "
        "as a beneficiary within the hour.", True),
    "night_frac": (
        "OFF_HOURS", "Off-hours activity",
        "{pct} of activity fell between midnight and 05:00.", True),
    "kyc_min_flag": (
        "WEAK_KYC", "Minimum-KYC account",
        "The account is on minimum KYC, which caps the recourse available "
        "if funds are disputed.", True),
    "account_vintage_days": (
        "NEW_ACCOUNT", "Recently opened",
        "The account is only {value:.0f} days old.", False),
    "median_in_to_out_min": (
        "FAST_FORWARD", "Immediate forwarding",
        "Median delay between a credit and the next debit is "
        "{value:.0f} minutes.", False),
    "amount_cv_out": (
        "UNIFORM_AMOUNTS", "Machine-like amounts",
        "Outgoing amounts are unusually uniform (CV {value:.2f}), suggesting "
        "scripted splitting rather than human spending.", False),
    "payee_concentration": (
        "SINGLE_PAYEE", "Concentrated outflow",
        "{pct} of all outflow went to one counterparty.", True),
    "reciprocity": (
        "ONE_WAY", "One-directional relationships",
        "Almost none of the counterparties ever received money back "
        "({pct} reciprocal) - genuine social payment circles are two-way.",
        False),
    "unique_payer_per_day": (
        "PAYER_RATE", "New payers per day",
        "{value:.1f} distinct new payers per active day.", True),
    "frac_atm_aeps": (
        "CASH_CHANNEL", "Cash-heavy channel mix",
        "{pct} of events used ATM or AePS.", True),
    "max_amount_1h": (
        "HOUR_VALUE", "Large one-hour value",
        "INR {value:,.0f} moved within a single hour.", True),
}

#: Graph and intelligence evidence is generated separately from SHAP.
GRAPH_REASONS = {
    "RING_MEMBERSHIP": ("Member of a detected fraud ring",
                        "One of {size} independently flagged accounts in a "
                        "{typology} cluster that moved INR {amount:,.0f} "
                        "between its members."),
    "SHARED_DEVICE_RING": ("Shares hardware with ring members",
                           "Shares {n} device(s) with other flagged accounts "
                           "in the same cluster."),
    "GRAPH_PROXIMITY": ("Close to known-bad accounts",
                        "Graph neural network places this account in a "
                        "neighbourhood with a mean risk of {risk:.0f}/100."),
}

INTEL_REASONS = {
    "NCRP_TICKET": ("Named in cyber-fraud complaints",
                    "{count} government cyber-fraud ticket(s) name this "
                    "account as the beneficiary, totalling INR {amount:,.0f}."),
    "APK_IOC": ("Hardcoded in a malicious APK",
                "BODHI SHIELD AI extracted this identifier from a "
                "{family} sample (confidence {conf:.0%})."),
    "REGULATORY_LIST": ("On a supervisory watch list",
                        "Present in a regulator-issued mule account list."),
    "TEMPORAL_PATTERN": ("Abnormal event sequence",
                         "The temporal model scores this account's event "
                         "ordering at {score:.0%} - credits and debits are "
                         "interleaved in a laundering pattern rather than a "
                         "spending pattern."),
}


def _fmt(template: str, value: float) -> str:
    return template.format(
        value=value,
        pct=f"{min(max(value, 0.0), 1.0):.0%}" if 0 <= value <= 1 else f"{value:.2f}",
        ratio=f"{value:.2f}",
        silence=value,
    )


def _severity(shap_value: float) -> str:
    a = abs(shap_value)
    if a >= 0.8:
        return "HIGH"
    if a >= 0.3:
        return "MEDIUM"
    if a >= 0.08:
        return "LOW"
    return "INFO"


def build_evidence(
    shap_reasons: list[tuple[str, float, float]],
    *,
    graph_score: float = 0.0,
    temporal_score: float = 0.0,
    ring: dict | None = None,
    intel: dict | None = None,
    max_items: int = 8,
) -> list[Evidence]:
    """Assemble the evidence list shown on an alert card."""
    out: list[Evidence] = []

    for feature, shap_value, feature_value in shap_reasons:
        entry = REASON_CATALOG.get(feature)
        if entry is None or shap_value <= 0:
            continue
        code, label, template, _ = entry
        out.append(Evidence(
            code=code, label=label,
            detail=_fmt(template, float(feature_value)),
            contribution=round(float(shap_value), 4),
            severity=_severity(shap_value),
        ))

    if ring:
        label, template = GRAPH_REASONS["RING_MEMBERSHIP"]
        out.append(Evidence(
            code="RING_MEMBERSHIP", label=label,
            detail=template.format(size=ring.get("size", 0),
                                   typology=ring.get("typology_hint", "MULE_CLUSTER"),
                                   amount=ring.get("total_amount", 0.0)),
            contribution=round(float(graph_score), 4), severity="HIGH",
        ))
        if ring.get("shared_devices"):
            label, template = GRAPH_REASONS["SHARED_DEVICE_RING"]
            out.append(Evidence(
                code="SHARED_DEVICE_RING", label=label,
                detail=template.format(n=len(ring["shared_devices"])),
                contribution=round(float(graph_score) * 0.5, 4), severity="HIGH",
            ))
    elif graph_score >= 0.5:
        label, template = GRAPH_REASONS["GRAPH_PROXIMITY"]
        out.append(Evidence(
            code="GRAPH_PROXIMITY", label=label,
            detail=template.format(risk=graph_score * 100),
            contribution=round(float(graph_score), 4), severity="MEDIUM",
        ))

    if temporal_score >= 0.5:
        label, template = INTEL_REASONS["TEMPORAL_PATTERN"]
        out.append(Evidence(
            code="TEMPORAL_PATTERN", label=label,
            detail=template.format(score=temporal_score),
            contribution=round(float(temporal_score), 4),
            severity="HIGH" if temporal_score > 0.75 else "MEDIUM",
        ))

    intel = intel or {}
    if intel.get("ncrp_ticket_count", 0) > 0:
        label, template = INTEL_REASONS["NCRP_TICKET"]
        out.append(Evidence(
            code="NCRP_TICKET", label=label,
            detail=template.format(count=int(intel["ncrp_ticket_count"]),
                                   amount=float(intel.get("ncrp_amount", 0.0))),
            contribution=1.0, severity="HIGH",
        ))
    if intel.get("ioc_hit", 0) > 0:
        label, template = INTEL_REASONS["APK_IOC"]
        out.append(Evidence(
            code="APK_IOC", label=label,
            detail=template.format(family=intel.get("malware_family", "unclassified"),
                                   conf=float(intel.get("ioc_max_confidence", 0.8))),
            contribution=0.9, severity="HIGH",
        ))
    if intel.get("regulatory_listed", 0) > 0:
        label, template = INTEL_REASONS["REGULATORY_LIST"]
        out.append(Evidence(code="REGULATORY_LIST", label=label, detail=template,
                            contribution=0.8, severity="HIGH"))

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    out.sort(key=lambda e: (order[e.severity], -abs(e.contribution)))
    return out[:max_items]


def compose_narrative(
    account_id: str, risk_score: float, evidence: list[Evidence],
    ring: dict | None = None, amount_at_risk: float = 0.0,
) -> str:
    """One paragraph an officer can paste into a case file."""
    band = band_for(risk_score)
    lead = (f"Account {account_id} scores {risk_score:.0f}/100 ({band}). ")
    if not evidence:
        return lead + "No individual indicator crossed its reporting threshold."

    top = evidence[:3]
    body = " ".join(e.detail for e in top)
    tail = ""
    # The ring sentence is already one of the evidence items when it made the
    # top three; repeating it verbatim in the same paragraph reads as padding.
    if ring and not any(e.code == "RING_MEMBERSHIP" for e in top):
        tail += (f" The account is one of {ring.get('size', 0)} flagged members of "
                 f"cluster {ring.get('ring_id', 'n/a')}, whose combined turnover is "
                 f"INR {ring.get('total_amount', 0.0):,.0f}.")
    elif ring:
        tail += f" Cluster reference {ring.get('ring_id', 'n/a')}."
    if amount_at_risk > 0:
        tail += f" Estimated exposure INR {amount_at_risk:,.0f}."
    if band in ("HIGH", "CRITICAL"):
        tail += (" Recommended: place a debit freeze pending customer contact and "
                 f"file an STR within {REGULATORY.str_filing_days} days if unexplained.")
    return lead + body + tail


def suggest_actions(risk_score: float, evidence: list[Evidence],
                    amount_at_risk: float = 0.0) -> list[ActionType]:
    """Proportionate response: never freeze an account on a weak signal."""
    codes = {e.code for e in evidence}
    actions: list[ActionType] = []

    if risk_score >= KILLSWITCH_THRESHOLD:
        actions.append(ActionType.FULL_FREEZE)
        actions.append(ActionType.REPORT_STR)
    elif risk_score >= 65:
        actions.append(ActionType.FREEZE_DEBIT)
        if amount_at_risk > 100_000 or "NCRP_TICKET" in codes:
            actions.append(ActionType.REPORT_STR)
    elif risk_score >= ALERT_THRESHOLD:
        actions.append(ActionType.HOLD_CREDIT if "FAN_IN" in codes
                       else ActionType.STEP_UP_AUTH)
    else:
        actions.append(ActionType.MONITOR)

    # Cash-out in progress is the one case where minutes matter.
    if {"CASH_OUT", "CASH_CHANNEL"} & codes and risk_score >= 65:
        if ActionType.FREEZE_DEBIT not in actions and ActionType.FULL_FREEZE not in actions:
            actions.insert(0, ActionType.FREEZE_DEBIT)
    return actions


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def top_shap(feature_names: list[str], contributions: np.ndarray,
             values: np.ndarray, k: int = 6) -> list[tuple[str, float, float]]:
    """Pick the k strongest risk-increasing attributions for one row."""
    idx = np.argsort(-contributions)[:k]
    return [(feature_names[i], float(contributions[i]), float(values[i]))
            for i in idx if contributions[i] > 1e-6]


__all__ = ["REASON_CATALOG", "GRAPH_REASONS", "INTEL_REASONS", "build_evidence",
           "compose_narrative", "suggest_actions", "top_shap", "now_utc"]
