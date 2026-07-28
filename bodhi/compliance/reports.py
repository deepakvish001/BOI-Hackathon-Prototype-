"""Suspicious Transaction Report generation (FIU-IND style).

Detection is only half the obligation. Under the PMLA a reporting entity must
file an STR within a defined window of forming the suspicion, and the report
has to state the *grounds*. That is why the explainability layer exists in a
form that can be serialised: the evidence list assembled for the investigator
is the same evidence list that populates the report's grounds-of-suspicion
section, so what the model "said" and what the bank filed cannot drift apart.

The output here is a structured draft for a human to review and file, not an
auto-submission. No prototype should file a regulatory report unattended, and
the ``status`` field says so explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from bodhi.config import REGULATORY
from bodhi.compliance.pii import redact_text
from bodhi.schemas import Alert


def build_str(
    alert: Alert,
    account_row: dict[str, Any] | None = None,
    transactions: pd.DataFrame | None = None,
    ring_members: list[str] | None = None,
    filed_by: str = "BODHI_MULE_HUNTER",
) -> dict[str, Any]:
    """Assemble a draft STR from an alert and its supporting transactions."""
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(days=REGULATORY.str_filing_days)

    txn_rows: list[dict[str, Any]] = []
    total = 0.0
    if transactions is not None and len(transactions):
        sub = transactions[
            (transactions["src_account"] == alert.account_id)
            | (transactions["dst_account"] == alert.account_id)
        ].sort_values("ts", ascending=False).head(50)
        for _, r in sub.iterrows():
            amount = float(r["amount"])
            total += amount
            txn_rows.append({
                "txn_id": str(r.get("txn_id", "")),
                "timestamp": str(r.get("timestamp", "")),
                "direction": "CREDIT" if r["dst_account"] == alert.account_id else "DEBIT",
                "counterparty": str(r["src_account"] if r["dst_account"] == alert.account_id
                                    else r["dst_account"]),
                "amount": round(amount, 2),
                "channel": str(r.get("channel", "")),
                "instrument": str(r.get("txn_type", "")),
            })

    grounds = [
        {"code": e.code, "ground": e.label, "detail": redact_text(e.detail)}
        for e in alert.evidence
    ]

    return {
        "report_type": "STR",
        "status": "DRAFT_PENDING_HUMAN_REVIEW",
        "reference": f"STR-{alert.alert_id}",
        "generated_at": now.isoformat(),
        "filing_deadline": deadline.isoformat(),
        "reporting_entity": {
            "name": "Bank of India",
            "bank_code": (account_row or {}).get("bank_code", "BKID"),
            "branch_code": (account_row or {}).get("branch_code"),
        },
        "subject": {
            "account_id": alert.account_id,
            "customer_id": (account_row or {}).get("customer_id"),
            "kyc_level": (account_row or {}).get("kyc_level"),
            "account_opened": str((account_row or {}).get("open_date", "")),
            "state": (account_row or {}).get("state"),
        },
        "suspicion": {
            "risk_score": alert.risk_score,
            "risk_band": alert.band.value,
            "summary": redact_text(alert.narrative),
            "grounds": grounds,
            "linked_government_tickets": alert.linked_tickets,
            "associated_accounts": ring_members or [],
        },
        "transactions": {
            "count": len(txn_rows),
            "total_value": round(total, 2),
            "amount_at_risk": alert.amount_at_risk,
            "detail": txn_rows,
        },
        "model_provenance": {
            "engine": "BODHI MULE HUNTER AI",
            "version": "1.0.0",
            "decision_basis": "Calibrated ensemble of gradient-boosted trees, "
                              "GraphSAGE and a temporal graph network; "
                              "attributions are exact TreeSHAP values.",
            "human_in_the_loop": True,
        },
        "prepared_by": filed_by,
    }


def str_to_text(report: dict[str, Any]) -> str:
    """Render a draft STR as plain text for review or printing."""
    s = report
    lines = [
        "SUSPICIOUS TRANSACTION REPORT (DRAFT)",
        "=" * 72,
        f"Reference        : {s['reference']}",
        f"Status           : {s['status']}",
        f"Generated        : {s['generated_at']}",
        f"Filing deadline  : {s['filing_deadline']}",
        "",
        "SUBJECT",
        "-" * 72,
        f"Account          : {s['subject']['account_id']}",
        f"Customer         : {s['subject'].get('customer_id')}",
        f"KYC level        : {s['subject'].get('kyc_level')}",
        f"Account opened   : {s['subject'].get('account_opened')}",
        "",
        "GROUNDS OF SUSPICION",
        "-" * 72,
        f"Risk score       : {s['suspicion']['risk_score']}/100 "
        f"({s['suspicion']['risk_band']})",
        "",
        s["suspicion"]["summary"],
        "",
    ]
    for i, g in enumerate(s["suspicion"]["grounds"], 1):
        lines.append(f"  {i}. [{g['code']}] {g['ground']}")
        lines.append(f"     {g['detail']}")
    if s["suspicion"]["linked_government_tickets"]:
        lines += ["", "Linked government cyber-fraud tickets:",
                  "  " + ", ".join(s["suspicion"]["linked_government_tickets"])]
    if s["suspicion"]["associated_accounts"]:
        lines += ["", "Associated accounts in the same cluster:",
                  "  " + ", ".join(s["suspicion"]["associated_accounts"][:20])]
    lines += [
        "",
        "TRANSACTIONS",
        "-" * 72,
        f"Records attached : {s['transactions']['count']}",
        f"Total value      : INR {s['transactions']['total_value']:,.2f}",
        f"Amount at risk   : INR {s['transactions']['amount_at_risk']:,.2f}",
        "",
        "PROVENANCE",
        "-" * 72,
        f"{s['model_provenance']['engine']} v{s['model_provenance']['version']}",
        s["model_provenance"]["decision_basis"],
        "",
        "This draft requires review and sign-off by an authorised officer "
        "before filing.",
    ]
    return "\n".join(lines)


def build_ctr_candidates(transactions: pd.DataFrame,
                         threshold: float | None = None) -> pd.DataFrame:
    """Cash transactions above the CTR threshold, aggregated per account-day.

    The PMLA threshold applies to the *aggregate* of cash transactions by one
    customer in one day, which is exactly why structuring works against a
    per-transaction check.
    """
    thr = threshold if threshold is not None else REGULATORY.ctr_threshold
    if not len(transactions):
        return pd.DataFrame(columns=["account_id", "date", "cash_total",
                                     "n_transactions"])
    cash = transactions[transactions["txn_type"].isin(
        ("CASH_WITHDRAWAL", "CASH_DEPOSIT"))].copy()
    if not len(cash):
        return pd.DataFrame(columns=["account_id", "date", "cash_total",
                                     "n_transactions"])
    cash["date"] = pd.to_datetime(cash["ts"], unit="s", utc=True).dt.date
    g = cash.groupby(["src_account", "date"]).agg(
        cash_total=("amount", "sum"), n_transactions=("amount", "size")).reset_index()
    g = g.rename(columns={"src_account": "account_id"})
    return g[g["cash_total"] >= thr].sort_values("cash_total", ascending=False)


__all__ = ["build_str", "str_to_text", "build_ctr_candidates"]
