"""Process-wide runtime state for the API.

Loads whatever artefacts exist and, if none do, builds a small demonstration
world on the fly so that ``uvicorn bodhi.api.main:app`` works on a clean
checkout with no prior steps. Judges should not have to run a training job
before they can click anything.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import pandas as pd

from bodhi.actions.casebook import Casebook
from bodhi.actions.killswitch import KillSwitch
from bodhi.compliance.audit import AuditLog
from bodhi.config import DATA_DIR, DataConfig, MODEL_DIR, RUNTIME_DIR, ensure_dirs
from bodhi.engine.pipeline import MuleHunterEngine
from bodhi.feeds.ncrp import NCRPConnector
from bodhi.feeds.regulatory import RegulatoryConnector

#: Size of the world built when no artefacts are present.
DEMO_CONFIG = DataConfig(n_accounts=4_000, n_days=70, n_devices=3_000,
                         n_merchants=200)


class RuntimeState:
    """Everything the API needs, assembled once per process."""

    def __init__(self) -> None:
        ensure_dirs()
        self.lock = threading.RLock()
        self.audit = AuditLog(RUNTIME_DIR / "audit.jsonl")
        self.engine = MuleHunterEngine()
        self.casebook = Casebook(audit=self.audit)
        self.killswitch = KillSwitch(audit=self.audit)
        self.ncrp = NCRPConnector()
        self.regulatory = RegulatoryConnector()
        self.simulation: Any = None
        self.ready = False
        self.bootstrap_mode = "uninitialised"
        self.metrics: dict[str, Any] = {}

    # -- bootstrap ---------------------------------------------------------

    def bootstrap(self, force_demo: bool = False) -> None:
        with self.lock:
            if self.ready:
                return
            from bodhi.data.generator import generate, load

            have_data = (DATA_DIR / "accounts.parquet").exists() or \
                        (DATA_DIR / "accounts.csv").exists()
            if have_data and not force_demo:
                sim = load(DATA_DIR)
                self.bootstrap_mode = "artifacts"
            else:
                sim = generate(DEMO_CONFIG)
                self.bootstrap_mode = "generated-demo"
            self.simulation = sim

            self.engine.attach(sim.accounts, sim.transactions, sim.fraud_alerts,
                               sim.iocs, sim.regulatory)
            self.ncrp = NCRPConnector(sim.fraud_alerts)
            self.regulatory = RegulatoryConnector(sim.regulatory)

            trained = (MODEL_DIR / "engine.json").exists()
            if trained and not force_demo:
                loaded = MuleHunterEngine.load(MODEL_DIR)
                loaded.attach(sim.accounts, sim.transactions, sim.fraud_alerts,
                              sim.iocs, sim.regulatory)
                self.engine = loaded
                self.engine.score_all()
                self.bootstrap_mode += "+loaded-models"
            else:
                self.engine.train(verbose=False)
                self.engine.score_all()
                self.bootstrap_mode += "+trained-on-boot"

            alerts = self.engine.generate_alerts(limit=120)
            self.casebook.add(alerts)
            self.audit.append("SYSTEM", "BOOTSTRAP", {
                "mode": self.bootstrap_mode,
                "accounts": int(len(sim.accounts)),
                "transactions": int(len(sim.transactions)),
                "alerts": len(alerts),
            })

            metrics_path = (RUNTIME_DIR.parent / "metrics" / "evaluation.json")
            if metrics_path.exists():
                import json
                try:
                    self.metrics = json.loads(metrics_path.read_text())
                except json.JSONDecodeError:
                    self.metrics = {}
            self.ready = True

    # -- convenience --------------------------------------------------------

    @property
    def accounts(self) -> pd.DataFrame:
        return self.engine.accounts

    @property
    def transactions(self) -> pd.DataFrame:
        return self.engine.transactions

    def account_row(self, account_id: str) -> dict[str, Any] | None:
        df = self.accounts
        hit = df[df["account_id"] == account_id]
        if not len(hit):
            return None
        return {k: (v.item() if hasattr(v, "item") else v)
                for k, v in hit.iloc[0].to_dict().items()}


_STATE: RuntimeState | None = None
_STATE_LOCK = threading.Lock()


def get_state() -> RuntimeState:
    """Singleton accessor used by the FastAPI dependency system."""
    global _STATE
    if _STATE is None:
        with _STATE_LOCK:
            if _STATE is None:
                _STATE = RuntimeState()
                if os.environ.get("BODHI_SKIP_BOOTSTRAP") != "1":
                    _STATE.bootstrap()
    return _STATE


def reset_state() -> None:
    """Used by tests to force a clean process-local state."""
    global _STATE
    with _STATE_LOCK:
        _STATE = None


__all__ = ["RuntimeState", "get_state", "reset_state", "DEMO_CONFIG"]
