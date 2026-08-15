"""Central configuration: paths, risk bands, regulatory thresholds, model knobs.

Every tunable used by more than one module lives here so that the pipeline,
the API and the evaluation scripts cannot silently disagree with each other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = Path(os.environ.get("BODHI_ARTIFACTS", ROOT / "artifacts"))

DATA_DIR = ARTIFACTS / "data"
MODEL_DIR = ARTIFACTS / "models"
METRICS_DIR = ARTIFACTS / "metrics"
FIGURE_DIR = ARTIFACTS / "figures"
RUNTIME_DIR = ARTIFACTS / "runtime"


def ensure_dirs() -> None:
    """Create every artifact directory. Safe to call repeatedly."""
    for d in (DATA_DIR, MODEL_DIR, METRICS_DIR, FIGURE_DIR, RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Team
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamMember:
    name: str
    enrolment: str


#: Single source of truth for authorship. The report (PDF/DOCX/LaTeX), the deck
#: (PPTX/PDF), the README and SUBMISSION.md all render from this tuple, so the
#: team list cannot end up different in one document from another.
TEAM: tuple[TeamMember, ...] = (
    TeamMember("Akshay Tiwari", "0246CS241037"),
    TeamMember("Palak Vishwakarma", "0246AL241124"),
    TeamMember("Archi Singh Rajput", "0246CS240174"),
    TeamMember("Kartik Jain", "0246AL241094"),
)

TEAM_NAME = "Team BODHI"
EVENT = "CyberShield Hackathon 2026 - Problem Statement 2"
AFFILIATION = "In association with Bank of India and IIT Hyderabad"


def team_inline(separator: str = "  |  ") -> str:
    """``Name (enrolment)`` for every member, joined on one line."""
    return separator.join(f"{m.name} ({m.enrolment})" for m in TEAM)


# --------------------------------------------------------------------------
# Risk bands  (mirrors the scoring table in the platform proposal)
# --------------------------------------------------------------------------

RISK_BANDS: list[tuple[int, int, str]] = [
    (0, 39, "SAFE"),
    (40, 64, "MEDIUM"),
    (65, 84, "HIGH"),
    (85, 100, "CRITICAL"),
]

#: Score at or above which an alert is raised to a human investigator.
ALERT_THRESHOLD = 40

#: Score at or above which the automated fraud kill-switch may act without a
#: human in the loop (still fully reversible + audited).
KILLSWITCH_THRESHOLD = 85


def band_for(score: float) -> str:
    """Map a 0-100 risk score onto its supervisory risk band."""
    s = max(0.0, min(100.0, float(score)))
    for lo, hi, name in RISK_BANDS:
        if lo <= s <= hi:
            return name
    return "CRITICAL"


# --------------------------------------------------------------------------
# Regulatory / domain thresholds (India)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RegulatoryConfig:
    """Thresholds drawn from Indian AML/payments practice.

    These are the numbers a structuring (``smurfing``) detector has to reason
    about, so they are configuration rather than magic numbers in the code.
    """

    #: Cash Transaction Report threshold (PMLA) in INR.
    ctr_threshold: float = 1_000_000.0
    #: Value above which UPI P2P is uncommon for retail customers, in INR.
    upi_p2p_cap: float = 100_000.0
    #: Classic structuring band - just under a reporting/limit boundary.
    structuring_band: tuple[float, float] = (45_000.0, 50_000.0)
    #: Days of inactivity after which an account is treated as dormant (RBI).
    dormancy_days: int = 180
    #: Cooling period a newly added beneficiary is considered "fresh", hours.
    fresh_beneficiary_hours: int = 24
    #: Suspicious Transaction Report must be filed within N days of detection.
    str_filing_days: int = 7


REGULATORY = RegulatoryConfig()


# --------------------------------------------------------------------------
# Feature-engineering windows
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureConfig:
    """Rolling windows (hours) over which behavioural features are computed."""

    short_window_h: int = 1
    medium_window_h: int = 24
    long_window_h: int = 24 * 7
    #: Window used for burst / velocity spikes.
    burst_window_min: int = 30
    #: A pass-through is "rapid" if money leaves within this many minutes.
    rapid_passthrough_min: int = 60
    #: Night-time band used for off-hours activity scoring (local hours).
    night_hours: tuple[int, int] = (0, 5)


FEATURES = FeatureConfig()


# --------------------------------------------------------------------------
# Model hyper-parameters
# --------------------------------------------------------------------------


@dataclass
class XGBConfig:
    """Layer 4 - gradient-boosted screening model."""

    n_estimators: int = 400
    max_depth: int = 6
    learning_rate: float = 0.06
    subsample: float = 0.85
    colsample_bytree: float = 0.8
    min_child_weight: float = 3.0
    reg_lambda: float = 2.0
    #: Cap on scale_pos_weight so the model stays calibrated.
    max_scale_pos_weight: float = 12.0
    random_state: int = 42


@dataclass
class GraphSAGEConfig:
    """Layer 5 - inductive graph neural network."""

    hidden_dims: tuple[int, ...] = (64, 32)
    fanouts: tuple[int, ...] = (15, 10)
    dropout: float = 0.2
    lr: float = 0.01
    weight_decay: float = 1e-5
    epochs: int = 60
    batch_size: int = 512
    random_state: int = 42


@dataclass
class TemporalConfig:
    """Layer 6 - temporal graph network over each account's event stream."""

    max_events: int = 64
    hidden_dim: int = 64
    time_dim: int = 16
    lr: float = 0.015
    weight_decay: float = 1e-5
    epochs: int = 90
    batch_size: int = 384
    random_state: int = 42


@dataclass
class FusionConfig:
    """Layer 7 - risk fusion stacker."""

    #: Fall-back weights used before the stacker is fitted.
    prior_weights: dict[str, float] = field(
        default_factory=lambda: {
            "xgb": 0.45,
            "graph": 0.25,
            "temporal": 0.20,
            "intel": 0.10,
        }
    )
    lr: float = 0.05
    epochs: int = 300
    random_state: int = 42


@dataclass
class ModelConfig:
    xgb: XGBConfig = field(default_factory=XGBConfig)
    graphsage: GraphSAGEConfig = field(default_factory=GraphSAGEConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)


MODELS = ModelConfig()


# --------------------------------------------------------------------------
# Synthetic data-generation defaults
# --------------------------------------------------------------------------


@dataclass
class DataConfig:
    """Shape of the simulated bank used by the prototype."""

    n_accounts: int = 12_000
    n_days: int = 120
    mule_rate: float = 0.012
    #: Fraction of the simulation window reserved for out-of-time testing.
    test_fraction: float = 0.25
    n_devices: int = 9_000
    n_merchants: int = 600
    seed: int = 7


DATA = DataConfig()


# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------

CHANNELS = ("UPI", "IMPS", "NEFT", "RTGS", "AEPS", "ATM", "CARD", "WALLET")

#: Channels through which money physically leaves the banking system.
CASHOUT_CHANNELS = ("ATM", "AEPS")

__all__ = [
    "TEAM",
    "TEAM_NAME",
    "EVENT",
    "AFFILIATION",
    "TeamMember",
    "team_inline",
    "ROOT",
    "ARTIFACTS",
    "DATA_DIR",
    "MODEL_DIR",
    "METRICS_DIR",
    "FIGURE_DIR",
    "RUNTIME_DIR",
    "ensure_dirs",
    "RISK_BANDS",
    "ALERT_THRESHOLD",
    "KILLSWITCH_THRESHOLD",
    "band_for",
    "REGULATORY",
    "FEATURES",
    "MODELS",
    "DATA",
    "CHANNELS",
    "CASHOUT_CHANNELS",
]
