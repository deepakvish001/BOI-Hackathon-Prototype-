"""Money-mule typologies the simulator reproduces.

Each entry describes a laundering pattern documented by FIU-IND / RBI / FATF
guidance. The simulator implements them literally so that detection quality can
be measured *per typology* rather than as a single averaged number - that
breakdown is what tells an investigator where the system is weak.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Typology:
    code: str
    name: str
    description: str
    #: Relative frequency used when sampling rings.
    weight: float
    #: Typical number of mule accounts involved in one ring.
    size_range: tuple[int, int]


TYPOLOGIES: tuple[Typology, ...] = (
    Typology(
        code="FAN_IN_AGGREGATION",
        name="Fan-in aggregation",
        description=(
            "Dozens of defrauded victims push money into a single collection "
            "account within a short window. The collector has no prior "
            "relationship with any payer."
        ),
        weight=0.22,
        size_range=(3, 8),
    ),
    Typology(
        code="LAYERING_CHAIN",
        name="Multi-hop layering chain",
        description=(
            "Funds are relayed A -> B -> C -> D within minutes, each hop "
            "retaining a small commission, to break the audit trail."
        ),
        weight=0.20,
        size_range=(4, 10),
    ),
    Typology(
        code="SMURFING",
        name="Structuring / smurfing",
        description=(
            "A large illicit sum is fragmented into many sub-threshold "
            "transfers sitting just under a reporting or channel limit."
        ),
        weight=0.16,
        size_range=(5, 14),
    ),
    Typology(
        code="DORMANT_BURST",
        name="Dormant reactivation burst",
        description=(
            "An account inactive for six months or more suddenly transacts at "
            "high velocity - the signature of a rented or purchased account."
        ),
        weight=0.14,
        size_range=(3, 9),
    ),
    Typology(
        code="DEVICE_FARM",
        name="Device farm / account rental",
        description=(
            "A single handler device or SIM operates a stable of accounts, "
            "producing an implausible device-to-account fan-out."
        ),
        weight=0.12,
        size_range=(6, 20),
    ),
    Typology(
        code="RAPID_CASHOUT",
        name="Rapid cash-out",
        description=(
            "Credits are drained to cash through ATM or AePS withdrawals "
            "within minutes, leaving near-zero end-of-day balance."
        ),
        weight=0.10,
        size_range=(3, 7),
    ),
    Typology(
        code="APK_HARVEST",
        name="Malicious-APK harvest network",
        description=(
            "Beneficiary accounts and UPI handles hardcoded inside a banking "
            "trojan. BODHI SHIELD AI extracts them; the graph is primed before "
            "the first rupee moves."
        ),
        weight=0.06,
        size_range=(4, 12),
    ),
)

TYPOLOGY_BY_CODE = {t.code: t for t in TYPOLOGIES}

__all__ = ["Typology", "TYPOLOGIES", "TYPOLOGY_BY_CODE"]
