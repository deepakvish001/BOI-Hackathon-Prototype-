"""Layer 8 - explainability: TreeSHAP, GNNExplainer and plain-English narrative."""

from bodhi.explain.gnn_explainer import EdgeAttribution, explain_node
from bodhi.explain.narrative import (
    REASON_CATALOG,
    build_evidence,
    compose_narrative,
    suggest_actions,
)

__all__ = [
    "build_evidence",
    "compose_narrative",
    "suggest_actions",
    "REASON_CATALOG",
    "explain_node",
    "EdgeAttribution",
]
