"""Layer 4 - gradient-boosted screening over tabular behaviour.

This is the workhorse: it disposes of the ~99% of accounts that are obviously
fine, cheaply, and it is the layer whose decisions we can explain exactly.
XGBoost computes true Shapley values for tree ensembles in polynomial time
(``pred_contribs=True``), so the attributions shown to an investigator are not
an approximation from a sampling-based explainer - they are exact, they sum to
the model's margin, and they are stable across runs. That matters when an
explanation may end up in a Suspicious Transaction Report.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from bodhi.config import MODELS, XGBConfig


class XGBScreener:
    """Binary mule classifier over behavioural + topological features."""

    def __init__(self, config: XGBConfig | None = None):
        self.config = config or MODELS.xgb
        self.booster: xgb.Booster | None = None
        self.feature_names: list[str] = []
        self.base_rate: float = 0.0

    # -- training -------------------------------------------------------

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        X_val: pd.DataFrame | None = None,
        y_val: np.ndarray | None = None,
        verbose: bool = False,
    ) -> "XGBScreener":
        c = self.config
        self.feature_names = list(X.columns)
        y = np.asarray(y).astype(int)
        self.base_rate = float(y.mean())

        pos = max(int(y.sum()), 1)
        neg = max(len(y) - pos, 1)
        # Capped: uncapped scale_pos_weight on a 1% base rate destroys
        # calibration, and a mis-calibrated score cannot drive a kill-switch.
        spw = min(neg / pos, c.max_scale_pos_weight)

        params = {
            "objective": "binary:logistic",
            "eval_metric": ["aucpr", "auc"],
            "max_depth": c.max_depth,
            "eta": c.learning_rate,
            "subsample": c.subsample,
            "colsample_bytree": c.colsample_bytree,
            "min_child_weight": c.min_child_weight,
            "lambda": c.reg_lambda,
            "scale_pos_weight": spw,
            "tree_method": "hist",
            "seed": c.random_state,
            "nthread": 0,
        }

        dtrain = xgb.DMatrix(X.to_numpy(dtype=np.float32), label=y,
                             feature_names=self.feature_names)
        evals = [(dtrain, "train")]
        early = None
        if X_val is not None and y_val is not None and len(X_val):
            dval = xgb.DMatrix(X_val.to_numpy(dtype=np.float32),
                               label=np.asarray(y_val).astype(int),
                               feature_names=self.feature_names)
            evals.append((dval, "val"))
            early = 40

        self.booster = xgb.train(
            params, dtrain,
            num_boost_round=c.n_estimators,
            evals=evals,
            early_stopping_rounds=early,
            verbose_eval=25 if verbose else False,
        )
        return self

    # -- inference ------------------------------------------------------

    def _dmatrix(self, X: pd.DataFrame) -> xgb.DMatrix:
        X = X.reindex(columns=self.feature_names, fill_value=0.0)
        return xgb.DMatrix(X.to_numpy(dtype=np.float32), feature_names=self.feature_names)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if self.booster is None:
            raise RuntimeError("XGBScreener is not fitted")
        return self.booster.predict(self._dmatrix(X))

    def shap_values(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Exact TreeSHAP contributions in log-odds space.

        Returns ``(contributions, bias)`` where ``contributions`` has one column
        per feature and ``contributions.sum(1) + bias`` equals the raw margin.
        """
        if self.booster is None:
            raise RuntimeError("XGBScreener is not fitted")
        contrib = self.booster.predict(self._dmatrix(X), pred_contribs=True)
        return contrib[:, :-1], contrib[:, -1]

    def top_reasons(
        self, X: pd.DataFrame, k: int = 6, direction: str = "positive"
    ) -> list[list[tuple[str, float, float]]]:
        """Per-row ``(feature, shap value, feature value)`` triples."""
        contrib, _ = self.shap_values(X)
        Xv = X.reindex(columns=self.feature_names, fill_value=0.0).to_numpy(dtype=float)
        out: list[list[tuple[str, float, float]]] = []
        for i in range(contrib.shape[0]):
            row = contrib[i]
            idx = np.argsort(-row) if direction == "positive" else np.argsort(np.abs(row))[::-1]
            picked = [j for j in idx[:k] if abs(row[j]) > 1e-9]
            out.append([(self.feature_names[j], float(row[j]), float(Xv[i, j]))
                        for j in picked])
        return out

    def global_importance(self) -> pd.Series:
        if self.booster is None:
            raise RuntimeError("XGBScreener is not fitted")
        gain = self.booster.get_score(importance_type="total_gain")
        s = pd.Series({f: gain.get(f, 0.0) for f in self.feature_names})
        total = s.sum()
        return (s / total if total else s).sort_values(ascending=False)

    # -- persistence ----------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.booster is None:
            raise RuntimeError("nothing to save")
        self.booster.save_model(str(path.with_suffix(".ubj")))
        path.with_suffix(".json").write_text(json.dumps({
            "feature_names": self.feature_names,
            "base_rate": self.base_rate,
        }))

    @classmethod
    def load(cls, path: Path) -> "XGBScreener":
        path = Path(path)
        obj = cls()
        booster = xgb.Booster()
        booster.load_model(str(path.with_suffix(".ubj")))
        obj.booster = booster
        meta = json.loads(path.with_suffix(".json").read_text())
        obj.feature_names = meta["feature_names"]
        obj.base_rate = meta.get("base_rate", 0.0)
        return obj


__all__ = ["XGBScreener"]
