"""The multi-agent view of the pipeline.

Each layer is owned by a named agent with one job, a declared input contract
and a declared output. This is not decoration: the audit trail records which
agent produced which artefact, so when a decision is challenged months later
the chain from raw event to frozen account can be replayed step by step.

Agents run in a fixed topological order. There is no LLM in the decision path -
every number that reaches a customer's account comes from a model whose
gradients are tested and whose attributions are exact.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    name: str
    layer: int
    role: str
    consumes: tuple[str, ...]
    produces: tuple[str, ...]


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec("TransactionMonitoringAgent", 1,
              "Normalises cross-channel events (UPI, IMPS, NEFT, RTGS, AePS, "
              "ATM, card) and government fraud tickets into one schema.",
              ("raw_feeds",), ("normalised_events",)),
    AgentSpec("FeatureEngineeringAgent", 2,
              "Derives velocity, fan-in/out, dormancy, burst, structuring and "
              "device-reuse features as of a point in time.",
              ("normalised_events",), ("feature_matrix",)),
    AgentSpec("GraphBuilderAgent", 3,
              "Maintains the account/device/IP knowledge graph incrementally.",
              ("normalised_events",), ("financial_graph",)),
    AgentSpec("FraudDetectionAgent", 4,
              "XGBoost screening over tabular behaviour; disposes of the "
              "obvious majority cheaply.",
              ("feature_matrix",), ("tabular_score", "shap_values")),
    AgentSpec("GraphIntelligenceAgent", 5,
              "GraphSAGE over the knowledge graph; surfaces rings and "
              "multi-hop structure invisible to tabular models.",
              ("financial_graph", "feature_matrix"), ("graph_score", "embeddings")),
    AgentSpec("TemporalIntelligenceAgent", 6,
              "TGN memory over each account's event sequence; detects "
              "smurfing, rapid routing and dormant-to-active bursts.",
              ("normalised_events",), ("temporal_score",)),
    AgentSpec("RiskFusionAgent", 7,
              "Calibrated stacking of all layer scores into one 0-100 number.",
              ("tabular_score", "graph_score", "temporal_score", "intel_score"),
              ("risk_score",)),
    AgentSpec("ExplainabilityAgent", 8,
              "TreeSHAP + GNNExplainer + narrative generation.",
              ("shap_values", "financial_graph", "risk_score"), ("evidence",)),
    AgentSpec("AlertGenerationAgent", 9,
              "Builds investigator cases, proposes proportionate actions and "
              "drives the reversible kill-switch.",
              ("risk_score", "evidence"), ("alerts", "actions")),
)

AGENT_BY_LAYER = {a.layer: a for a in AGENTS}


@dataclass
class AgentTrace:
    """Timing and provenance record for one pipeline execution."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)

    def record(self, layer: int, detail: str = "", **extra: Any) -> None:
        spec = AGENT_BY_LAYER.get(layer)
        self.steps.append({
            "layer": layer,
            "agent": spec.name if spec else f"Layer{layer}",
            "elapsed_ms": round((time.perf_counter() - self._t0) * 1000, 2),
            "detail": detail,
            **extra,
        })

    def total_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 2)

    def to_list(self) -> list[dict[str, Any]]:
        return list(self.steps)


__all__ = ["AGENTS", "AGENT_BY_LAYER", "AgentSpec", "AgentTrace"]
