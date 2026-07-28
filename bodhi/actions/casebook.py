"""Investigator case management.

The alert queue is where the system meets its users, and the metric that
matters there is not AUC - it is how many cases an officer can close per shift
and how many of those closures were correct. So the casebook records
disposition (confirmed fraud / false positive) and feeds it back: the
``feedback_labels`` method returns exactly the supervision needed to retrain,
which is how a deployed system stops decaying.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bodhi.compliance.audit import AuditLog
from bodhi.schemas import Alert, AlertStatus


class Casebook:
    """In-memory case store with optional JSONL persistence."""

    def __init__(self, audit: AuditLog | None = None, path: Path | None = None):
        self.alerts: dict[str, Alert] = {}
        self.notes: dict[str, list[dict[str, Any]]] = {}
        self.audit = audit or AuditLog()
        self.path = Path(path) if path else None

    # -- creation ----------------------------------------------------------

    def add(self, alerts: list[Alert] | Alert) -> int:
        items = [alerts] if isinstance(alerts, Alert) else list(alerts)
        added = 0
        for a in items:
            if a.alert_id in self.alerts:
                continue
            self.alerts[a.alert_id] = a
            added += 1
            self.audit.append("ALERT_AGENT", "ALERT_CREATED", {
                "alert_id": a.alert_id, "account_id": a.account_id,
                "risk_score": a.risk_score, "band": a.band.value,
                "evidence_codes": [e.code for e in a.evidence],
            })
        if self.path:
            self._persist()
        return added

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            for a in self.alerts.values():
                fh.write(json.dumps(a.model_dump(mode="json")) + "\n")

    # -- workflow -----------------------------------------------------------

    def assign(self, alert_id: str, investigator: str) -> Alert | None:
        alert = self.alerts.get(alert_id)
        if alert is None:
            return None
        alert.assigned_to = investigator
        alert.status = AlertStatus.IN_REVIEW
        self.audit.append(investigator, "ALERT_ASSIGNED", {"alert_id": alert_id})
        return alert

    def resolve(self, alert_id: str, status: AlertStatus, investigator: str,
                note: str = "") -> Alert | None:
        alert = self.alerts.get(alert_id)
        if alert is None:
            return None
        alert.status = status
        if note:
            self.add_note(alert_id, investigator, note)
        self.audit.append(investigator, "ALERT_RESOLVED", {
            "alert_id": alert_id, "account_id": alert.account_id,
            "status": status.value, "note": note,
        })
        if self.path:
            self._persist()
        return alert

    def add_note(self, alert_id: str, author: str, text: str) -> None:
        self.notes.setdefault(alert_id, []).append({
            "author": author, "text": text,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    # -- queries -------------------------------------------------------------

    def queue(self, status: AlertStatus | None = None, limit: int = 100,
              min_score: float = 0.0) -> list[Alert]:
        items = [a for a in self.alerts.values() if a.risk_score >= min_score]
        if status is not None:
            items = [a for a in items if a.status == status]
        items.sort(key=lambda a: (a.risk_score, a.amount_at_risk), reverse=True)
        return items[:limit]

    def get(self, alert_id: str) -> Alert | None:
        return self.alerts.get(alert_id)

    def for_account(self, account_id: str) -> list[Alert]:
        return [a for a in self.alerts.values() if a.account_id == account_id]

    # -- feedback loop --------------------------------------------------------

    def feedback_labels(self) -> pd.DataFrame:
        """Investigator dispositions, ready to be folded into the next retrain."""
        rows = []
        for a in self.alerts.values():
            if a.status == AlertStatus.CONFIRMED_FRAUD:
                label = 1
            elif a.status == AlertStatus.FALSE_POSITIVE:
                label = 0
            else:
                continue
            rows.append({
                "account_id": a.account_id,
                "label": label,
                "alert_id": a.alert_id,
                "risk_score": a.risk_score,
                "resolved_by": a.assigned_to,
            })
        return pd.DataFrame(rows, columns=["account_id", "label", "alert_id",
                                           "risk_score", "resolved_by"])

    def stats(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_band: dict[str, int] = {}
        for a in self.alerts.values():
            by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
            by_band[a.band.value] = by_band.get(a.band.value, 0) + 1
        resolved = [a for a in self.alerts.values()
                    if a.status in (AlertStatus.CONFIRMED_FRAUD,
                                    AlertStatus.FALSE_POSITIVE)]
        confirmed = [a for a in resolved if a.status == AlertStatus.CONFIRMED_FRAUD]
        scores = np.array([a.risk_score for a in self.alerts.values()]) \
            if self.alerts else np.array([0.0])
        return {
            "total_alerts": len(self.alerts),
            "by_status": by_status,
            "by_band": by_band,
            "resolved": len(resolved),
            "confirmed_fraud": len(confirmed),
            "precision_on_resolved": (round(len(confirmed) / len(resolved), 4)
                                      if resolved else None),
            "total_amount_at_risk": round(
                sum(a.amount_at_risk for a in self.alerts.values()), 2),
            "median_score": round(float(np.median(scores)), 2),
        }


__all__ = ["Casebook"]
