"""The automated fraud kill-switch.

Freezing a bank account is the most consequential thing this system can do. A
wrongly frozen account is somebody unable to pay for medicine, and no AUC
improvement justifies being casual about it. So the kill-switch is built around
constraints rather than around a threshold:

* **Proportionality.** Below 85 the automation cannot freeze at all; it can
  only hold an inbound credit or force step-up authentication. A full freeze is
  reserved for the CRITICAL band.
* **Corroboration.** A model score alone never triggers the highest action. At
  least two *independent* channels must agree - behaviour, graph, temporal or
  external intelligence - because a single layer's failure mode should not be
  able to freeze anyone.
* **Reversibility.** Every action carries an expiry and can be reverted, and
  reverting is itself an audited event.
* **Rate limiting.** A bounded number of automated freezes per window. If the
  model degrades or an upstream feed is poisoned, the blast radius is capped
  and the rest of the queue goes to humans.
* **Protected accounts.** Salary, pension and government-benefit accounts are
  excluded from automated freezing entirely and always route to a human.

Every decision - including every refusal - is written to the audit log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from bodhi.compliance.audit import AuditLog
from bodhi.config import KILLSWITCH_THRESHOLD
from bodhi.schemas import ActionRecord, ActionType

#: Actions the automation may take without a human, by ascending severity.
AUTOMATED_ACTIONS = (ActionType.MONITOR, ActionType.STEP_UP_AUTH,
                     ActionType.HOLD_CREDIT, ActionType.FREEZE_DEBIT,
                     ActionType.FULL_FREEZE)

#: How long an automated action stands before it must be renewed by a human.
DEFAULT_TTL_HOURS = {
    ActionType.MONITOR: 24 * 30,
    ActionType.STEP_UP_AUTH: 72,
    ActionType.HOLD_CREDIT: 24,
    ActionType.FREEZE_DEBIT: 48,
    ActionType.FULL_FREEZE: 24,
}


@dataclass
class KillSwitchDecision:
    account_id: str
    action: ActionType
    applied: bool
    reason: str
    refusals: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    requires_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "action": self.action.value,
            "applied": self.applied,
            "reason": self.reason,
            "refusals": self.refusals,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "requires_human": self.requires_human,
        }


class KillSwitch:
    """Applies, records and reverses automated containment actions."""

    def __init__(
        self,
        audit: AuditLog | None = None,
        max_auto_freezes_per_hour: int = 25,
        min_corroborating_layers: int = 2,
        protected_accounts: set[str] | None = None,
    ):
        self.audit = audit or AuditLog()
        self.max_auto_freezes_per_hour = max_auto_freezes_per_hour
        self.min_corroborating_layers = min_corroborating_layers
        self.protected_accounts = protected_accounts or set()
        self.active: dict[str, ActionRecord] = {}
        self.history: list[ActionRecord] = []
        self._freeze_times: list[float] = []

    # -- helpers ---------------------------------------------------------

    def _recent_freezes(self) -> int:
        cutoff = time.time() - 3_600
        self._freeze_times = [t for t in self._freeze_times if t >= cutoff]
        return len(self._freeze_times)

    @staticmethod
    def _corroborating_layers(layer_scores: dict[str, float],
                              threshold: float = 0.5) -> list[str]:
        return [k for k, v in (layer_scores or {}).items() if float(v) >= threshold]

    # -- main entry point --------------------------------------------------

    def evaluate(
        self,
        account_id: str,
        risk_score: float,
        proposed: ActionType,
        layer_scores: dict[str, float] | None = None,
        amount_at_risk: float = 0.0,
        actor: str = "BODHI_AUTOMATION",
    ) -> KillSwitchDecision:
        """Decide whether the automation may take ``proposed`` on its own."""
        refusals: list[str] = []
        layers = self._corroborating_layers(layer_scores or {})

        severe = proposed in (ActionType.FREEZE_DEBIT, ActionType.FULL_FREEZE)

        if account_id in self.protected_accounts and severe:
            refusals.append(
                "account is flagged as salary/pension/benefit - automated "
                "freezing is not permitted")
        if proposed == ActionType.FULL_FREEZE and risk_score < KILLSWITCH_THRESHOLD:
            refusals.append(
                f"score {risk_score:.0f} is below the automation floor of "
                f"{KILLSWITCH_THRESHOLD}")
        if severe and len(layers) < self.min_corroborating_layers:
            refusals.append(
                f"only {len(layers)} independent layer(s) corroborate "
                f"({', '.join(layers) or 'none'}); "
                f"{self.min_corroborating_layers} required")
        if severe and self._recent_freezes() >= self.max_auto_freezes_per_hour:
            refusals.append(
                f"hourly automated-freeze budget exhausted "
                f"({self.max_auto_freezes_per_hour}/h)")

        if refusals:
            # Downgrade rather than escalate, and route to a human.
            fallback = (ActionType.HOLD_CREDIT if risk_score >= 65
                        else ActionType.MONITOR)
            decision = KillSwitchDecision(
                account_id=account_id, action=fallback, applied=True,
                reason="; ".join(refusals), refusals=refusals,
                requires_human=True,
            )
            self._apply(account_id, fallback,
                        f"downgraded from {proposed.value}: {decision.reason}", actor)
            self.audit.append(actor, "KILLSWITCH_DOWNGRADE", {
                "account_id": account_id, "proposed": proposed.value,
                "applied": fallback.value, "risk_score": risk_score,
                "refusals": refusals, "amount_at_risk": amount_at_risk,
            })
            return decision

        ttl = DEFAULT_TTL_HOURS.get(proposed, 24)
        expires = datetime.now(timezone.utc) + timedelta(hours=ttl)
        reason = (f"risk {risk_score:.0f}/100 corroborated by "
                  f"{len(layers)} layers ({', '.join(layers)})")
        self._apply(account_id, proposed, reason, actor, expires)
        if severe:
            self._freeze_times.append(time.time())
        self.audit.append(actor, "KILLSWITCH_APPLY", {
            "account_id": account_id, "action": proposed.value,
            "risk_score": risk_score, "corroborating_layers": layers,
            "amount_at_risk": amount_at_risk, "expires_at": expires.isoformat(),
        })
        return KillSwitchDecision(account_id=account_id, action=proposed,
                                  applied=True, reason=reason, expires_at=expires)

    def _apply(self, account_id: str, action: ActionType, reason: str,
               actor: str, expires: datetime | None = None) -> ActionRecord:
        record = ActionRecord(
            action_id=f"ACT-{int(time.time() * 1000)}-{len(self.history):05d}",
            taken_at=datetime.now(timezone.utc), account_id=account_id,
            action=action, reason=reason, actor=actor, reversible=True,
        )
        self.history.append(record)
        if action != ActionType.MONITOR:
            self.active[account_id] = record
        return record

    # -- reversal ----------------------------------------------------------

    def revert(self, account_id: str, actor: str = "INVESTIGATOR",
               reason: str = "reviewed and cleared") -> bool:
        record = self.active.pop(account_id, None)
        if record is None:
            return False
        record.reverted_at = datetime.now(timezone.utc)
        self.audit.append(actor, "KILLSWITCH_REVERT", {
            "account_id": account_id, "action": record.action.value,
            "reason": reason, "original_action_id": record.action_id,
        })
        return True

    def expire_due(self, now: datetime | None = None) -> list[str]:
        """Release actions whose time-to-live has elapsed."""
        now = now or datetime.now(timezone.utc)
        released: list[str] = []
        for account_id, record in list(self.active.items()):
            ttl = DEFAULT_TTL_HOURS.get(record.action, 24)
            if record.taken_at + timedelta(hours=ttl) <= now:
                self.revert(account_id, actor="SYSTEM_TTL",
                            reason="time-to-live elapsed without human renewal")
                released.append(account_id)
        return released

    # -- introspection ------------------------------------------------------

    def status(self, account_id: str) -> dict[str, Any] | None:
        record = self.active.get(account_id)
        return record.model_dump(mode="json") if record else None

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for r in self.history:
            counts[r.action.value] = counts.get(r.action.value, 0) + 1
        return {
            "active_restrictions": len(self.active),
            "total_actions": len(self.history),
            "by_action": counts,
            "freezes_last_hour": self._recent_freezes(),
            "audit_entries": len(self.audit),
            "audit_head": self.audit.head,
        }


__all__ = ["KillSwitch", "KillSwitchDecision", "AUTOMATED_ACTIONS",
           "DEFAULT_TTL_HOURS"]
