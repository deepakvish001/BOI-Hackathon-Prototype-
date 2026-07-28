"""The nine-layer pipeline: training, batch scoring and real-time decisions.

Training order matters and is not arbitrary:

1. Behavioural features are built first, with no graph input.
2. A *behaviour-only* XGBoost is fitted with 5-fold cross-fitting and used to
   seed the ``neighbour_risk_mean`` topology feature. Doing it this way avoids
   the circularity of "my neighbours' risk depends on my risk", and the
   cross-fitting stops a training node's memorised label leaking into a test
   node's neighbourhood average. See :meth:`_cross_fitted_seed_risk`.
3. Topology features are appended and the real XGBoost is fitted.
4. GraphSAGE and the TGN are fitted on the same node split.
5. The fusion blend is fitted on a **dedicated fourth fold** that no base model
   has seen - not on the validation fold, which early-stopped them and whose
   optimism they would inherit.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from bodhi.config import (
    ALERT_THRESHOLD,
    MODEL_DIR,
    MODELS,
    band_for,
)
from bodhi.explain.narrative import (
    build_evidence,
    compose_narrative,
    now_utc,
    suggest_actions,
)
from bodhi.features.engineering import (
    BEHAVIOUR_FEATURES,
    INTEL_FEATURES,
    build_account_features,
    build_intel_features,
    has_intelligence,
)
from bodhi.graph.builder import TOPOLOGY_FEATURES, build_graph, topology_features
from bodhi.graph.rings import Ring, detect_rings
from bodhi.models.fusion import RiskFusion
from bodhi.models.graphsage import GraphSAGE
from bodhi.models.temporal import TemporalGraphNetwork, build_sequences
from bodhi.models.xgb_model import XGBScreener
from bodhi.schemas import Alert, RiskBand, TransactionDecision

MODEL_FEATURES: tuple[str, ...] = tuple(BEHAVIOUR_FEATURES) + tuple(TOPOLOGY_FEATURES)


@dataclass
class ScoringResult:
    """Everything the pipeline produces for one scoring pass."""

    scores: pd.DataFrame                 # account_id -> per-layer + fused
    rings: list[Ring] = field(default_factory=list)
    as_of: float = 0.0
    elapsed_ms: float = 0.0

    def top(self, k: int = 25) -> pd.DataFrame:
        return self.scores.nlargest(k, "risk_score")


class MuleHunterEngine:
    """Owns the models, the graph and the current view of the world."""

    version = "1.0.0"

    def __init__(self) -> None:
        self.xgb = XGBScreener()
        self.sage = GraphSAGE()
        self.tgn = TemporalGraphNetwork()
        self.fusion = RiskFusion()
        self.intel_model: LogisticRegression | None = None
        #: Behaviour-only model that seeds ``neighbour_risk_mean``. It is part
        #: of the persisted artefact: without it a reloaded engine would build
        #: a different feature matrix and quietly return different scores.
        self.seed_model: XGBScreener | None = None

        self.accounts: pd.DataFrame | None = None
        self.transactions: pd.DataFrame | None = None
        self.fraud_alerts: pd.DataFrame | None = None
        self.iocs: pd.DataFrame | None = None
        self.regulatory: pd.DataFrame | None = None

        self.graph = None
        self.features: pd.DataFrame | None = None
        self.last_result: ScoringResult | None = None
        self.split: dict[str, np.ndarray] = {}
        self.train_report: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # data attachment
    # ------------------------------------------------------------------

    def attach(
        self,
        accounts: pd.DataFrame,
        transactions: pd.DataFrame,
        fraud_alerts: pd.DataFrame | None = None,
        iocs: pd.DataFrame | None = None,
        regulatory: pd.DataFrame | None = None,
    ) -> "MuleHunterEngine":
        self.accounts = accounts.reset_index(drop=True)
        self.transactions = transactions.sort_values("ts").reset_index(drop=True)
        self.fraud_alerts = fraud_alerts
        self.iocs = iocs
        self.regulatory = regulatory
        return self

    # ------------------------------------------------------------------
    # feature assembly
    # ------------------------------------------------------------------

    def build_features(self, as_of: float | None = None,
                       seed_risk: np.ndarray | None = None) -> pd.DataFrame:
        """Behavioural + topological features, aligned to the account table.

        When no explicit ``seed_risk`` is supplied the persisted seed model
        produces it. Deriving it from the previous scoring run instead would
        make the score depend on process history: the same account, same data,
        would score differently after a restart. For a system that must be able
        to reproduce a decision months later in front of a regulator, that is
        not acceptable.
        """
        behaviour = build_account_features(self.transactions, self.accounts, as_of)
        if seed_risk is None and self.seed_model is not None:
            seed_risk = self.seed_model.predict_proba(behaviour)
        self.graph = build_graph(self.transactions, self.accounts, as_of)
        topo = topology_features(self.graph, prior_risk=seed_risk)
        X = behaviour.join(topo)
        return X.reindex(columns=list(MODEL_FEATURES)).fillna(0.0)

    def _intel_frame(self, as_of: float | None = None) -> pd.DataFrame:
        return build_intel_features(self.accounts, self.fraud_alerts, self.iocs,
                                    self.regulatory, as_of)

    # ------------------------------------------------------------------
    # training
    # ------------------------------------------------------------------

    def train(
        self,
        label_col: str = "is_mule",
        as_of: float | None = None,
        test_size: float = 0.2,
        val_size: float = 0.15,
        fuse_size: float = 0.15,
        seed: int = 42,
        verbose: bool = True,
    ) -> dict[str, Any]:
        """Fit every layer.

        Four folds, not three. ``train`` fits the base models, ``val`` drives
        their early stopping, ``fuse`` fits the Layer-7 stacker and ``test`` is
        touched exactly once at the end. The separate fusion fold exists
        because a stacker fitted on the fold that early-stopped its own inputs
        inherits their optimism and reliably comes out *worse* than the best
        single layer.
        """
        if self.accounts is None:
            raise RuntimeError("call attach() before train()")
        t_start = time.perf_counter()
        y = self.accounts[label_col].to_numpy().astype(int)
        idx = np.arange(len(y))

        rest, test_idx = train_test_split(
            idx, test_size=test_size, stratify=y, random_state=seed)
        rest2, fuse_idx = train_test_split(
            rest, test_size=fuse_size / (1 - test_size),
            stratify=y[rest], random_state=seed)
        train_idx, val_idx = train_test_split(
            rest2, test_size=val_size / (1 - test_size - fuse_size),
            stratify=y[rest2], random_state=seed)
        self.split = {"train": train_idx, "val": val_idx,
                      "fuse": fuse_idx, "test": test_idx}

        # -- pass 1: behaviour-only seed model for neighbour risk ----------
        if verbose:
            print("[1/6] behavioural features ...")
        behaviour = build_account_features(self.transactions, self.accounts, as_of)
        seed_risk = self._cross_fitted_seed_risk(behaviour, y, train_idx, seed)

        # -- pass 2: full feature matrix -----------------------------------
        if verbose:
            print("[2/6] graph + topology features ...")
        X = self.build_features(as_of, seed_risk=seed_risk)
        self.features = X

        # -- Layer 4 --------------------------------------------------------
        if verbose:
            print("[3/6] Layer 4  XGBoost screening ...")
        self.xgb = XGBScreener(MODELS.xgb)
        self.xgb.fit(X.iloc[train_idx], y[train_idx], X.iloc[val_idx], y[val_idx])
        p_xgb = self.xgb.predict_proba(X)

        # -- Layer 5 --------------------------------------------------------
        if verbose:
            print("[4/6] Layer 5  GraphSAGE ...")
        self.sage = GraphSAGE(MODELS.graphsage)
        self.sage.fit(self.graph, X.to_numpy(dtype=float), y, train_idx, val_idx,
                      verbose=verbose)
        p_sage = self.sage.predict_proba(X.to_numpy(dtype=float))

        # -- Layer 6 --------------------------------------------------------
        if verbose:
            print("[5/6] Layer 6  temporal graph network ...")
        Xt, Mt = build_sequences(self.transactions, self.accounts,
                                 MODELS.temporal.max_events, as_of)
        self.tgn = TemporalGraphNetwork(MODELS.temporal)
        self.tgn.fit(Xt, Mt, y, train_idx, val_idx, verbose=verbose)
        p_tgn = self.tgn.predict_proba(Xt, Mt)
        self._sequences = (Xt, Mt)

        # -- intelligence channel --------------------------------------------
        intel = self._intel_frame(as_of)
        self.intel_model = LogisticRegression(max_iter=1_000, class_weight="balanced")
        Xi = intel.to_numpy(dtype=float)
        if len(np.unique(y[train_idx])) > 1:
            self.intel_model.fit(Xi[train_idx], y[train_idx])
            p_intel = self.intel_model.predict_proba(Xi)[:, 1]
        else:  # pragma: no cover
            p_intel = np.zeros(len(y))
        # An account nobody has reported must not receive a positive intel
        # score just because the logistic intercept is high.
        p_intel = np.where(has_intelligence(intel), p_intel, 0.0)

        # -- Layer 7: fit the stacker on the validation fold ------------------
        if verbose:
            print("[6/6] Layer 7  risk fusion ...")
        sub = {"xgb": p_xgb, "graph": p_sage, "temporal": p_tgn, "intel": p_intel}
        self.fusion = RiskFusion(MODELS.fusion)
        self.fusion.fit({k: v[fuse_idx] for k, v in sub.items()}, y[fuse_idx])

        fused = self.fusion.risk_score(sub)
        self.last_result = ScoringResult(
            scores=self._assemble_scores(sub, fused),
            as_of=float(as_of if as_of is not None else self.transactions["ts"].max()),
        )

        self.train_report = {
            "n_accounts": int(len(y)),
            "n_mules": int(y.sum()),
            "split": {k: int(len(v)) for k, v in self.split.items()},
            "features": len(MODEL_FEATURES),
            "train_seconds": round(time.perf_counter() - t_start, 2),
            "graphsage_history": self.sage.history[-1] if self.sage.history else {},
            "tgn_history": self.tgn.history[-1] if self.tgn.history else {},
        }
        return self.train_report

    def _cross_fitted_seed_risk(self, behaviour: pd.DataFrame, y: np.ndarray,
                                train_idx: np.ndarray, seed: int,
                                n_folds: int = 5) -> np.ndarray:
        """Seed risk used by the ``neighbour_risk_mean`` topology feature.

        The naive version - fit on the training nodes, predict everywhere -
        quietly poisons the whole evaluation. A training node's seed prediction
        is near-perfect because the model memorised its label, so a *test* mule
        whose neighbours happen to be training mules inherits a high
        neighbourhood risk that no deployed system could reproduce on a genuinely
        new account.

        Cross-fitting removes that: each training node is scored by a model that
        never saw it. Held-out nodes are scored by the full model, which is
        exactly the production situation. The feature keeps its (real) power to
        propagate confirmed-fraud signal across the graph without smuggling
        labels across the split.
        """
        from sklearn.model_selection import StratifiedKFold

        seed_risk = np.zeros(len(y))
        train_y = y[train_idx]
        n_folds = min(n_folds, max(2, int(train_y.sum())))

        if train_y.sum() >= n_folds:
            skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=seed)
            for inner_tr, inner_te in skf.split(train_idx, train_y):
                m = XGBScreener(MODELS.xgb)
                m.config.n_estimators = 150
                m.fit(behaviour.iloc[train_idx[inner_tr]], train_y[inner_tr])
                seed_risk[train_idx[inner_te]] = m.predict_proba(
                    behaviour.iloc[train_idx[inner_te]])

        full = XGBScreener(MODELS.xgb)
        full.config.n_estimators = 150
        full.fit(behaviour.iloc[train_idx], train_y)
        held_out = np.setdiff1d(np.arange(len(y)), train_idx)
        seed_risk[held_out] = full.predict_proba(behaviour.iloc[held_out])
        self.seed_model = full
        return seed_risk

    def _assemble_scores(self, sub: dict[str, np.ndarray],
                         fused: np.ndarray) -> pd.DataFrame:
        df = pd.DataFrame({
            "account_id": self.accounts["account_id"].to_numpy(),
            "score_xgb": sub["xgb"],
            "score_graph": sub["graph"],
            "score_temporal": sub["temporal"],
            "score_intel": sub["intel"],
            # The calibrated probability, kept alongside the 0-100 display
            # score. ``risk_score`` is a monotone square-root warp of this, so
            # calibration must be measured against ``prob_mule`` - measuring it
            # against risk_score/100 reports the error of the warp, not of the
            # model.
            "prob_mule": self.fusion.predict_proba(sub),
            "risk_score": fused,
        }).set_index("account_id")
        df["band"] = [band_for(s) for s in df["risk_score"]]
        return df

    # ------------------------------------------------------------------
    # batch scoring
    # ------------------------------------------------------------------

    def score_all(self, as_of: float | None = None,
                  detect_rings_too: bool = True) -> ScoringResult:
        """Re-score every account, optionally re-clustering rings."""
        t0 = time.perf_counter()
        X = self.build_features(as_of)
        self.features = X

        p_xgb = self.xgb.predict_proba(X)
        p_sage = self.sage.predict_proba(X.to_numpy(dtype=float), graph=self.graph)
        Xt, Mt = build_sequences(self.transactions, self.accounts,
                                 MODELS.temporal.max_events, as_of)
        self._sequences = (Xt, Mt)
        p_tgn = self.tgn.predict_proba(Xt, Mt)

        intel = self._intel_frame(as_of)
        Xi = intel.to_numpy(dtype=float)
        if self.intel_model is not None:
            p_intel = self.intel_model.predict_proba(Xi)[:, 1]
            p_intel = np.where(has_intelligence(intel), p_intel, 0.0)
        else:
            p_intel = np.zeros(len(Xi))

        sub = {"xgb": p_xgb, "graph": p_sage, "temporal": p_tgn, "intel": p_intel}
        fused = self.fusion.risk_score(sub)
        scores = self._assemble_scores(sub, fused)

        rings: list[Ring] = []
        if detect_rings_too:
            rings = detect_rings(self.graph, scores["risk_score"], threshold=55.0)

        self.last_result = ScoringResult(
            scores=scores, rings=rings,
            as_of=float(as_of if as_of is not None else self.transactions["ts"].max()),
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 2),
        )
        return self.last_result

    # ------------------------------------------------------------------
    # explanation & alerting
    # ------------------------------------------------------------------

    def explain_account(self, account_id: str, k: int = 8) -> dict[str, Any]:
        """Full Layer-8 output for one account."""
        if self.last_result is None or self.features is None:
            raise RuntimeError("run train() or score_all() first")
        if account_id not in self.features.index:
            raise KeyError(account_id)

        row = self.features.loc[[account_id]]
        reasons = self.xgb.top_reasons(row, k=k)[0]
        scores = self.last_result.scores.loc[account_id]

        ring = None
        for r in self.last_result.rings:
            if account_id in r.members:
                ring = r.to_dict()
                break

        intel_row = self._intel_frame(self.last_result.as_of).loc[account_id]
        intel = {c: float(intel_row[c]) for c in INTEL_FEATURES}
        family = None
        if self.iocs is not None and len(self.iocs):
            vpa = self.accounts.loc[
                self.accounts["account_id"] == account_id, "vpa"]
            hit = self.iocs[self.iocs["value"].isin(
                [account_id, *(vpa.tolist() if len(vpa) else [])])]
            if len(hit):
                family = str(hit.iloc[0].get("malware_family") or "unclassified")
        if family:
            intel["malware_family"] = family

        evidence = build_evidence(
            reasons,
            graph_score=float(scores["score_graph"]),
            temporal_score=float(scores["score_temporal"]),
            ring=ring, intel=intel, max_items=k,
        )
        exposure = self._exposure(account_id)
        return {
            "account_id": account_id,
            "risk_score": float(scores["risk_score"]),
            "band": str(scores["band"]),
            "layer_scores": {
                "xgb": float(scores["score_xgb"]),
                "graph": float(scores["score_graph"]),
                "temporal": float(scores["score_temporal"]),
                "intel": float(scores["score_intel"]),
            },
            "evidence": [e.model_dump() for e in evidence],
            "ring": ring,
            "narrative": compose_narrative(account_id, float(scores["risk_score"]),
                                           evidence, ring, exposure),
            "suggested_actions": [a.value for a in
                                  suggest_actions(float(scores["risk_score"]),
                                                  evidence, exposure)],
            "amount_at_risk": exposure,
            "shap": [{"feature": f, "contribution": round(c, 4), "value": round(v, 4)}
                     for f, c, v in reasons],
        }

    #: Window over which unrecovered credits are treated as still recoverable.
    EXPOSURE_WINDOW_DAYS = 7

    def _exposure(self, account_id: str) -> float:
        """Recent credits that have not yet been moved on.

        This is the money a freeze could still actually save, which is a very
        different number from the account's lifetime turnover. Anything older
        than the window has been laundered and is gone.
        """
        tx = self.transactions
        cut = float(tx["ts"].max()) - self.EXPOSURE_WINDOW_DAYS * 86_400
        recent = tx[(tx["ts"] >= cut)]
        credited = float(recent.loc[recent["dst_account"] == account_id, "amount"].sum())
        debited = float(recent.loc[recent["src_account"] == account_id, "amount"].sum())
        return round(max(credited - debited, 0.0), 2)

    def generate_alerts(self, threshold: float = ALERT_THRESHOLD,
                        limit: int = 200) -> list[Alert]:
        """Convert high scores into investigator cases."""
        if self.last_result is None:
            raise RuntimeError("score_all() first")
        hits = self.last_result.scores
        hits = hits[hits["risk_score"] >= threshold].nlargest(limit, "risk_score")

        alerts: list[Alert] = []
        for i, (account_id, row) in enumerate(hits.iterrows()):
            detail = self.explain_account(str(account_id))
            ring = detail["ring"]
            title = self._title(detail)
            alerts.append(Alert(
                alert_id=f"ALT-{int(time.time())}-{i:04d}",
                created_at=now_utc(),
                account_id=str(account_id),
                risk_score=round(float(row["risk_score"]), 2),
                band=RiskBand(str(row["band"])),
                title=title,
                narrative=detail["narrative"],
                evidence=[e for e in build_evidence(
                    [(s["feature"], s["contribution"], s["value"]) for s in detail["shap"]],
                    graph_score=detail["layer_scores"]["graph"],
                    temporal_score=detail["layer_scores"]["temporal"],
                    ring=ring,
                )],
                ring=None,
                suggested_actions=suggest_actions(
                    float(row["risk_score"]),
                    build_evidence([(s["feature"], s["contribution"], s["value"])
                                    for s in detail["shap"]]),
                    detail["amount_at_risk"]),
                linked_tickets=self._linked_tickets(str(account_id)),
                amount_at_risk=detail["amount_at_risk"],
            ))
        return alerts

    def _title(self, detail: dict) -> str:
        ring = detail["ring"]
        if ring:
            return (f"{ring['typology_hint'].replace('_', ' ').title()} - "
                    f"{ring['size']}-account ring")
        ev = detail["evidence"]
        if ev:
            return f"{ev[0]['label']} - account {detail['account_id']}"
        return f"Elevated risk - account {detail['account_id']}"

    def _linked_tickets(self, account_id: str) -> list[str]:
        if self.fraud_alerts is None or not len(self.fraud_alerts):
            return []
        hit = self.fraud_alerts[self.fraud_alerts["beneficiary_account"] == account_id]
        return [str(t) for t in hit["ticket_id"].head(10)]

    # ------------------------------------------------------------------
    # real-time transaction decision
    # ------------------------------------------------------------------

    def score_transaction(self, txn: dict) -> TransactionDecision:
        """Sub-millisecond verdict for an in-flight payment.

        The heavy layers already produced a standing risk for both parties, so
        the online path is a cheap combination of those cached scores with the
        transaction's own attributes. That is what makes an inline decision
        feasible at UPI latencies - the graph is never re-traversed here.
        """
        t0 = time.perf_counter()
        if self.last_result is None:
            raise RuntimeError("score_all() first")
        scores = self.last_result.scores

        src, dst = str(txn["src_account"]), str(txn["dst_account"])
        src_risk = float(scores["risk_score"].get(src, 0.0))
        dst_risk = float(scores["risk_score"].get(dst, 0.0))
        amount = float(txn["amount"])
        ben_age = float(txn.get("beneficiary_age_hours") or 9_999.0)
        channel = str(txn.get("channel", "UPI"))

        from bodhi.schemas import Evidence
        reasons: list[Evidence] = []
        risk = max(src_risk * 0.35, dst_risk)

        if dst_risk >= 65:
            reasons.append(Evidence(
                code="HIGH_RISK_BENEFICIARY", label="Beneficiary already flagged",
                detail=f"Destination account scores {dst_risk:.0f}/100.",
                contribution=dst_risk / 100, severity="HIGH"))
        if ben_age < 1.0 and amount >= 25_000:
            risk = min(100.0, risk + 18)
            reasons.append(Evidence(
                code="FRESH_BENEFICIARY", label="Large payment to a minutes-old payee",
                detail=f"Beneficiary added {ben_age * 60:.0f} minutes ago for an "
                       f"INR {amount:,.0f} transfer.",
                contribution=0.18, severity="HIGH"))
        if channel in ("AEPS", "ATM") and src_risk >= 55:
            risk = min(100.0, risk + 12)
            reasons.append(Evidence(
                code="CASH_OUT_ATTEMPT", label="Cash-out from a flagged account",
                detail=f"{channel} withdrawal of INR {amount:,.0f} from an account "
                       f"scoring {src_risk:.0f}/100.",
                contribution=0.12, severity="HIGH"))

        band = band_for(risk)
        if risk >= 85:
            decision = "BLOCK"
        elif risk >= 65:
            decision = "HOLD"
        elif risk >= ALERT_THRESHOLD:
            decision = "REVIEW"
        else:
            decision = "ALLOW"

        return TransactionDecision(
            txn_id=str(txn.get("txn_id", "LIVE")),
            decision=decision, risk_score=round(risk, 2), band=RiskBand(band),
            reasons=reasons, src_risk=round(src_risk, 2), dst_risk=round(dst_risk, 2),
            latency_ms=round((time.perf_counter() - t0) * 1000, 3),
            scored_at=now_utc(),
        )

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def save(self, directory: Path | None = None) -> Path:
        d = Path(directory or MODEL_DIR)
        d.mkdir(parents=True, exist_ok=True)
        self.xgb.save(d / "xgb")
        if self.seed_model is not None:
            self.seed_model.save(d / "seed_xgb")
        self.sage.save(d / "graphsage")
        self.tgn.save(d / "tgn")
        self.fusion.save(d / "fusion")
        if self.intel_model is not None:
            np.savez(d / "intel.npz", coef=self.intel_model.coef_,
                     intercept=self.intel_model.intercept_,
                     classes=self.intel_model.classes_)
        (d / "engine.json").write_text(json.dumps({
            "version": self.version,
            "features": list(MODEL_FEATURES),
            "train_report": self.train_report,
        }, indent=2, default=str))
        return d

    @classmethod
    def load(cls, directory: Path | None = None) -> "MuleHunterEngine":
        d = Path(directory or MODEL_DIR)
        obj = cls()
        obj.xgb = XGBScreener.load(d / "xgb")
        if (d / "seed_xgb.ubj").exists():
            obj.seed_model = XGBScreener.load(d / "seed_xgb")
        obj.sage = GraphSAGE.load(d / "graphsage")
        obj.tgn = TemporalGraphNetwork.load(d / "tgn")
        obj.fusion = RiskFusion.load(d / "fusion")
        intel_path = d / "intel.npz"
        if intel_path.exists():
            data = np.load(intel_path)
            m = LogisticRegression()
            m.coef_, m.intercept_, m.classes_ = (data["coef"], data["intercept"],
                                                 data["classes"])
            obj.intel_model = m
        meta = d / "engine.json"
        if meta.exists():
            obj.train_report = json.loads(meta.read_text()).get("train_report", {})
        return obj


__all__ = ["MuleHunterEngine", "ScoringResult", "MODEL_FEATURES"]
