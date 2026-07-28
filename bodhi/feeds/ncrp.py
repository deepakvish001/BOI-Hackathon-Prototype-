"""Government cyber-fraud ticket ingestion (NCRP / I4C / 1930 helpline).

The problem statement asks the system to consume "govt cyber fraud
alerts/tickets". Those tickets are the highest-precision signal a bank ever
gets - a named victim, a named beneficiary, a rupee amount - and also the
latest-arriving: complaints land hours to days after the money has already
been layered and cashed out.

So this connector treats tickets as **corroboration, never as the trigger**.
Three consequences are enforced in code:

1. A ticket sets the intelligence channel only; it is never fed to the
   supervised models, which keeps the "we caught mules nobody reported"
   claim measurable rather than circular.
2. ``reported_at`` is honoured strictly. Back-dating a ticket into the feature
   window would make every back-test look brilliant and every deployment look
   broken.
3. Disputed and withdrawn complaints exist. A single ticket raises risk; it
   does not by itself justify freezing an account.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

from bodhi.schemas import FraudAlert

#: Categories that indicate genuine cyber-enabled financial fraud, as opposed
#: to a commercial dispute between two legitimate parties.
FRAUD_CATEGORIES = {
    "UPI_FRAUD", "APK_TROJAN", "OTP_FRAUD", "INVESTMENT_SCAM",
    "DIGITAL_ARREST", "JOB_SCAM", "LOAN_APP_HARASSMENT", "SEXTORTION",
    "CARD_NOT_PRESENT",
}
#: Categories that should not, on their own, drive an enforcement action.
SOFT_CATEGORIES = {"DISPUTED_TXN", "GOODS_NOT_DELIVERED", "SERVICE_DISPUTE"}


def normalise_ticket(raw: dict[str, Any]) -> FraudAlert:
    """Map a heterogeneous upstream payload onto the internal schema."""
    reported = raw.get("reported_at") or raw.get("complaint_date") or \
        datetime.now(timezone.utc)
    if isinstance(reported, (int, float)):
        reported = datetime.fromtimestamp(float(reported), tz=timezone.utc)
    elif isinstance(reported, str):
        reported = pd.to_datetime(reported, utc=True).to_pydatetime()

    beneficiary = (raw.get("beneficiary_account") or raw.get("suspect_account")
                   or raw.get("credited_account") or "")
    return FraudAlert(
        ticket_id=str(raw.get("ticket_id") or raw.get("ack_no") or "UNKNOWN"),
        reported_at=reported,
        source=raw.get("source", "NCRP"),
        victim_account=raw.get("victim_account") or raw.get("complainant_account"),
        beneficiary_account=str(beneficiary),
        beneficiary_vpa=raw.get("beneficiary_vpa") or raw.get("suspect_vpa"),
        amount=float(raw.get("amount") or raw.get("fraud_amount") or 0.0),
        fraud_category=str(raw.get("fraud_category") or raw.get("category")
                           or "UPI_FRAUD"),
        narrative=str(raw.get("narrative") or raw.get("description") or ""),
    )


class NCRPConnector:
    """Accumulates tickets and answers point-in-time questions about them."""

    def __init__(self, tickets: pd.DataFrame | None = None):
        self.tickets = tickets if tickets is not None else pd.DataFrame(
            columns=["ticket_id", "reported_at", "source", "victim_account",
                     "beneficiary_account", "beneficiary_vpa", "amount",
                     "fraud_category", "narrative"])

    def ingest(self, raw: Iterable[dict[str, Any]] | list[FraudAlert]) -> int:
        """Add tickets, de-duplicating on ticket id. Returns the count added."""
        rows = []
        for item in raw:
            alert = item if isinstance(item, FraudAlert) else normalise_ticket(item)
            rows.append(alert.model_dump())
        if not rows:
            return 0
        new = pd.DataFrame(rows)
        combined = pd.concat([self.tickets, new], ignore_index=True)
        before = len(self.tickets)
        self.tickets = combined.drop_duplicates(subset=["ticket_id"], keep="last") \
                               .reset_index(drop=True)
        return len(self.tickets) - before

    def as_of(self, timestamp: float) -> pd.DataFrame:
        """Only tickets the bank could actually have known about by then."""
        if not len(self.tickets):
            return self.tickets
        rep = pd.to_datetime(self.tickets["reported_at"], utc=True)
        return self.tickets[rep.astype("int64") / 1e9 <= timestamp]

    def hard_tickets(self) -> pd.DataFrame:
        """Tickets alleging cyber fraud, excluding commercial disputes."""
        if not len(self.tickets):
            return self.tickets
        return self.tickets[self.tickets["fraud_category"].isin(FRAUD_CATEGORIES)]

    def beneficiary_summary(self) -> pd.DataFrame:
        """Per-beneficiary ticket counts, victims and exposure."""
        hard = self.hard_tickets()
        if not len(hard):
            return pd.DataFrame(columns=["beneficiary_account", "tickets",
                                         "distinct_victims", "total_amount"])
        g = hard.groupby("beneficiary_account")
        return pd.DataFrame({
            "tickets": g.size(),
            "distinct_victims": g["victim_account"].nunique(),
            "total_amount": g["amount"].sum(),
        }).reset_index().sort_values("tickets", ascending=False)

    def lookback_hours(self, account_id: str, hours: float,
                       now_ts: float) -> pd.DataFrame:
        """Tickets naming this account inside a recent window."""
        recent = self.as_of(now_ts)
        if not len(recent):
            return recent
        rep = pd.to_datetime(recent["reported_at"], utc=True).astype("int64") / 1e9
        mask = ((recent["beneficiary_account"] == account_id) &
                (rep >= now_ts - hours * 3_600))
        return recent[mask]


__all__ = ["NCRPConnector", "normalise_ticket", "FRAUD_CATEGORIES", "SOFT_CATEGORIES"]
