"""Real-time regulatory and scheme-operator feeds (RBI, NPCI, CERT-In, FIU-IND).

The problem statement calls for consuming "real-time regulatory inputs/feeds".
In practice those arrive as four distinct things, and conflating them is how
compliance projects go wrong:

``MULE_ACCOUNT_LIST``
    Named accounts a supervisor has already identified. Directly raises the
    intelligence channel for those accounts *and* their one-hop neighbours -
    a mule's counterparties are the rest of the ring.
``VELOCITY_LIMIT``
    Operational caps (per-day P2P ceilings, first-24-hour limits on a new
    beneficiary). These change *thresholds*, not scores, and are applied at
    decision time.
``SANCTION``
    Hard blocks. No score, no discretion, no threshold.
``ADVISORY`` / ``TYPOLOGY``
    Narrative guidance. Recorded for the audit trail and surfaced to
    investigators; deliberately **not** auto-applied, because a machine cannot
    responsibly translate prose into an enforcement rule.

Directives carry a publication timestamp and are only ever applied from that
instant forward.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd

from bodhi.schemas import RegulatoryFeedItem

#: Fallback operational limits when no directive has been received.
DEFAULT_LIMITS: dict[str, float] = {
    "max_p2p_per_day": 100_000.0,
    "max_new_beneficiary_first_24h": 5_000.0,
    "max_aeps_per_day": 50_000.0,
}


class RegulatoryConnector:
    """Holds directives and answers "what is in force right now?"."""

    def __init__(self, items: pd.DataFrame | None = None):
        self.items = items if items is not None else pd.DataFrame(
            columns=["feed_id", "published_at", "authority", "directive_type",
                     "entities", "payload"])

    def ingest(self, raw: Iterable[dict[str, Any]] | list[RegulatoryFeedItem]) -> int:
        rows = []
        for item in raw:
            if isinstance(item, RegulatoryFeedItem):
                d = item.model_dump()
                d["entities"] = ",".join(item.entities)
                d["payload"] = json.dumps(item.payload)
            else:
                d = dict(item)
                if isinstance(d.get("entities"), (list, tuple)):
                    d["entities"] = ",".join(str(e) for e in d["entities"])
                if isinstance(d.get("payload"), dict):
                    d["payload"] = json.dumps(d["payload"])
                d.setdefault("published_at", datetime.now(timezone.utc))
            rows.append(d)
        if not rows:
            return 0
        before = len(self.items)
        self.items = pd.concat([self.items, pd.DataFrame(rows)], ignore_index=True) \
                       .drop_duplicates(subset=["feed_id"], keep="last") \
                       .reset_index(drop=True)
        return len(self.items) - before

    def as_of(self, timestamp: float) -> pd.DataFrame:
        if not len(self.items):
            return self.items
        pub = pd.to_datetime(self.items["published_at"], utc=True)
        return self.items[pub.astype("int64") / 1e9 <= timestamp]

    def listed_accounts(self, timestamp: float | None = None) -> set[str]:
        """Accounts named in any in-force mule list."""
        frame = self.as_of(timestamp) if timestamp is not None else self.items
        if not len(frame):
            return set()
        out: set[str] = set()
        for _, row in frame.iterrows():
            if row.get("directive_type") != "MULE_ACCOUNT_LIST":
                continue
            out.update(e for e in str(row.get("entities") or "").split(",") if e)
        return out

    def sanctioned_accounts(self, timestamp: float | None = None) -> set[str]:
        frame = self.as_of(timestamp) if timestamp is not None else self.items
        if not len(frame):
            return set()
        out: set[str] = set()
        for _, row in frame.iterrows():
            if row.get("directive_type") == "SANCTION":
                out.update(e for e in str(row.get("entities") or "").split(",") if e)
        return out

    def limits(self, timestamp: float | None = None) -> dict[str, float]:
        """Most recent operational limits in force."""
        frame = self.as_of(timestamp) if timestamp is not None else self.items
        limits = dict(DEFAULT_LIMITS)
        if not len(frame):
            return limits
        vel = frame[frame["directive_type"] == "VELOCITY_LIMIT"]
        if not len(vel):
            return limits
        vel = vel.sort_values("published_at")
        for _, row in vel.iterrows():
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if isinstance(payload, dict):
                for k, v in payload.items():
                    if isinstance(v, (int, float)):
                        limits[k] = float(v)
        return limits

    def advisories(self, timestamp: float | None = None) -> list[dict[str, Any]]:
        frame = self.as_of(timestamp) if timestamp is not None else self.items
        if not len(frame):
            return []
        adv = frame[frame["directive_type"].isin(("ADVISORY", "TYPOLOGY"))]
        out = []
        for _, row in adv.iterrows():
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {"text": payload}
            out.append({
                "feed_id": row.get("feed_id"),
                "authority": row.get("authority"),
                "published_at": str(row.get("published_at")),
                "payload": payload,
            })
        return out


def apply_directives(
    risk: pd.Series,
    connector: RegulatoryConnector,
    neighbours: dict[str, list[str]] | None = None,
    timestamp: float | None = None,
    listed_floor: float = 75.0,
    neighbour_bonus: float = 8.0,
) -> pd.Series:
    """Overlay supervisory directives onto a computed risk vector.

    Listed accounts are raised to a floor rather than pinned to a constant, so
    a listed account that *also* looks bad behaviourally still outranks one
    that merely appears on the list. Sanctions are absolute.
    """
    out = risk.copy().astype(float)
    listed = connector.listed_accounts(timestamp)
    if listed:
        idx = out.index.intersection(list(listed))
        out.loc[idx] = np.maximum(out.loc[idx].to_numpy(), listed_floor)
        if neighbours:
            bumped: set[str] = set()
            for account in idx:
                bumped.update(neighbours.get(account, []))
            bumped -= set(idx)
            nidx = out.index.intersection(list(bumped))
            out.loc[nidx] = np.minimum(out.loc[nidx].to_numpy() + neighbour_bonus, 100.0)

    sanctioned = connector.sanctioned_accounts(timestamp)
    if sanctioned:
        sidx = out.index.intersection(list(sanctioned))
        out.loc[sidx] = 100.0
    return out.clip(0.0, 100.0)


__all__ = ["RegulatoryConnector", "apply_directives", "DEFAULT_LIMITS"]
