"""Pydantic contracts for every object that crosses a module or API boundary.

Keeping these in one place means the ingestion layer, the scoring engine, the
dashboard and the regulatory reporter all speak the same language.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Channel(str, Enum):
    UPI = "UPI"
    IMPS = "IMPS"
    NEFT = "NEFT"
    RTGS = "RTGS"
    AEPS = "AEPS"
    ATM = "ATM"
    CARD = "CARD"
    WALLET = "WALLET"


class TxnType(str, Enum):
    P2P = "P2P"
    P2M = "P2M"
    CASH_WITHDRAWAL = "CASH_WITHDRAWAL"
    CASH_DEPOSIT = "CASH_DEPOSIT"
    SALARY = "SALARY"
    BILL = "BILL"


class RiskBand(str, Enum):
    SAFE = "SAFE"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    CLOSED = "CLOSED"


class ActionType(str, Enum):
    MONITOR = "MONITOR"
    STEP_UP_AUTH = "STEP_UP_AUTH"
    HOLD_CREDIT = "HOLD_CREDIT"
    FREEZE_DEBIT = "FREEZE_DEBIT"
    FULL_FREEZE = "FULL_FREEZE"
    REPORT_STR = "REPORT_STR"


class KycLevel(str, Enum):
    FULL = "FULL"
    VIDEO = "VIDEO"
    MIN = "MIN"


# --------------------------------------------------------------------------
# Core entities
# --------------------------------------------------------------------------


class Account(BaseModel):
    """A customer account inside (or reachable from) the bank."""

    account_id: str
    customer_id: str
    bank_code: str = "BKID"
    branch_code: str = "0001"
    open_date: datetime
    kyc_level: KycLevel = KycLevel.FULL
    customer_age: int = 30
    income_band: Literal["LOW", "MID", "HIGH"] = "MID"
    state: str = "MH"
    is_internal: bool = True
    #: Ground truth, only populated in simulation / back-testing.
    is_mule: bool | None = None
    mule_typology: str | None = None


class Transaction(BaseModel):
    """A single normalised financial event (Layer 1 output)."""

    txn_id: str
    timestamp: datetime
    src_account: str
    dst_account: str
    amount: float = Field(gt=0)
    channel: Channel = Channel.UPI
    txn_type: TxnType = TxnType.P2P
    device_id: str | None = None
    ip_address: str | None = None
    src_vpa: str | None = None
    dst_vpa: str | None = None
    beneficiary_age_hours: float | None = Field(
        default=None,
        description="Hours since the beneficiary was added to the payer's list.",
    )
    is_fraud: bool | None = None

    @field_validator("amount")
    @classmethod
    def _round_amount(cls, v: float) -> float:
        return round(float(v), 2)


class DeviceLink(BaseModel):
    device_id: str
    account_id: str
    first_seen: datetime
    last_seen: datetime
    os: str = "Android"


# --------------------------------------------------------------------------
# External intelligence feeds
# --------------------------------------------------------------------------


class FraudAlert(BaseModel):
    """A government cyber-fraud complaint (NCRP / I4C / 1930 helpline ticket).

    This is the ``govt cyber fraud alerts/tickets`` input of the problem
    statement: a victim names a beneficiary account that received their money.
    """

    ticket_id: str
    reported_at: datetime
    source: Literal["NCRP", "I4C", "HELPLINE_1930", "BANK_CFR", "PARTNER_BANK"] = "NCRP"
    victim_account: str | None = None
    beneficiary_account: str
    beneficiary_vpa: str | None = None
    amount: float = 0.0
    fraud_category: str = "UPI_FRAUD"
    narrative: str = ""


class IoC(BaseModel):
    """An indicator of compromise handed over by BODHI SHIELD AI (APK analysis)."""

    ioc_id: str
    ioc_type: Literal["UPI_VPA", "ACCOUNT", "DEVICE", "IP", "URL", "SHA256", "PHONE"]
    value: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source_apk_sha256: str | None = None
    malware_family: str | None = None
    observed_at: datetime | None = None


class RegulatoryFeedItem(BaseModel):
    """An entry from a regulatory / supervisory feed consumed in real time."""

    feed_id: str
    published_at: datetime
    authority: Literal["RBI", "NPCI", "CERT_IN", "FIU_IND", "SEBI"] = "RBI"
    directive_type: Literal[
        "MULE_ACCOUNT_LIST", "SANCTION", "VELOCITY_LIMIT", "ADVISORY", "TYPOLOGY"
    ] = "ADVISORY"
    entities: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Scoring outputs
# --------------------------------------------------------------------------


class LayerScores(BaseModel):
    """Per-layer sub-scores, all on a 0-1 probability scale."""

    xgb: float = 0.0
    graph: float = 0.0
    temporal: float = 0.0
    intel: float = 0.0


class Evidence(BaseModel):
    """One human-readable reason contributing to a decision."""

    code: str
    label: str
    detail: str
    contribution: float = 0.0
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH"] = "MEDIUM"


class RingSummary(BaseModel):
    ring_id: str
    size: int
    members: list[str]
    total_amount: float = 0.0
    density: float = 0.0
    shared_devices: list[str] = Field(default_factory=list)
    typology_hint: str | None = None


class AccountRisk(BaseModel):
    """The unified verdict for one account (Layer 7 + 8 output)."""

    account_id: str
    risk_score: float = Field(ge=0.0, le=100.0)
    band: RiskBand
    layer_scores: LayerScores = Field(default_factory=LayerScores)
    evidence: list[Evidence] = Field(default_factory=list)
    ring: RingSummary | None = None
    suggested_actions: list[ActionType] = Field(default_factory=list)
    scored_at: datetime
    model_version: str = "1.0.0"


class TransactionDecision(BaseModel):
    """Real-time verdict for one in-flight transaction."""

    txn_id: str
    decision: Literal["ALLOW", "REVIEW", "HOLD", "BLOCK"]
    risk_score: float
    band: RiskBand
    reasons: list[Evidence] = Field(default_factory=list)
    src_risk: float = 0.0
    dst_risk: float = 0.0
    latency_ms: float = 0.0
    scored_at: datetime


class Alert(BaseModel):
    """A case pushed to the investigator dashboard (Layer 9)."""

    alert_id: str
    created_at: datetime
    account_id: str
    risk_score: float
    band: RiskBand
    title: str
    narrative: str
    evidence: list[Evidence] = Field(default_factory=list)
    ring: RingSummary | None = None
    suggested_actions: list[ActionType] = Field(default_factory=list)
    status: AlertStatus = AlertStatus.OPEN
    assigned_to: str | None = None
    linked_tickets: list[str] = Field(default_factory=list)
    amount_at_risk: float = 0.0


class ActionRecord(BaseModel):
    """An action taken by the kill-switch or by an investigator."""

    action_id: str
    taken_at: datetime
    account_id: str
    action: ActionType
    reason: str
    actor: str = "BODHI_AUTOMATION"
    reversible: bool = True
    reverted_at: datetime | None = None


class AuditEntry(BaseModel):
    """One link in the tamper-evident audit chain."""

    seq: int
    timestamp: datetime
    actor: str
    event: str
    payload_digest: str
    prev_hash: str
    entry_hash: str


# --------------------------------------------------------------------------
# API request/response envelopes
# --------------------------------------------------------------------------


class ScoreTransactionRequest(BaseModel):
    transaction: Transaction


class ScoreAccountRequest(BaseModel):
    account_id: str


class IngestBatchRequest(BaseModel):
    transactions: list[Transaction] = Field(default_factory=list)
    fraud_alerts: list[FraudAlert] = Field(default_factory=list)
    iocs: list[IoC] = Field(default_factory=list)
    regulatory: list[RegulatoryFeedItem] = Field(default_factory=list)


class IngestBatchResponse(BaseModel):
    accepted_transactions: int
    accepted_alerts: int
    accepted_iocs: int
    accepted_regulatory: int
    new_alerts: list[Alert] = Field(default_factory=list)
    processing_ms: float = 0.0


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    version: str
    models_loaded: bool
    accounts_in_graph: int
    transactions_seen: int
    open_alerts: int


__all__ = [n for n in dir() if not n.startswith("_")]
