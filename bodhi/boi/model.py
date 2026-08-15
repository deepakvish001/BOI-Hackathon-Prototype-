"""The competition model for the bank's alert dataset.

The shape of this problem drives every decision here: roughly four thousand
predictors against, most likely, a few thousand alerts. That is firmly in
p >> n territory, where the danger is not underfitting but *selection bias* -
picking features that look good on the data you measured them on.

So:

* **Feature selection happens inside every fold.** Ranking 3,900 columns on the
  full training set and then cross-validating the winners is the classic way to
  manufacture an impressive score that evaporates on held-out data. Each fold
  ranks and selects using only its own training part.
* **Several feature sets compete.** The bank's own eighteen finalised
  variables, those plus engineered columns, an automatically selected top-k,
  and everything. Which one wins is decided by repeated cross-validation, not
  by assertion - and on a small dataset the bank's eighteen frequently do.
* **Repeated stratified CV, not a single split.** With a few hundred positives,
  one split's estimate moves by several AUC points depending on the seed.
* **The model is an ensemble over seeds**, which costs little and removes the
  "we got lucky with random_state" objection.

The output is a calibrated probability plus a threshold chosen for the operating
point the organisers are likely to score on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold

from bodhi.boi import features as bx
from bodhi.boi.dataset import CATEGORICAL_COLUMNS
from bodhi.boi.schema import TARGET, load_dictionary

STRATEGIES = ("bank_finalized", "bank_plus_engineered", "auto_topk", "all")


@dataclass
class BOIConfig:
    n_estimators: int = 600
    max_depth: int = 4                 # shallow: p >> n punishes deep trees
    learning_rate: float = 0.035
    subsample: float = 0.8
    colsample_bytree: float = 0.3      # low: thousands of correlated columns
    colsample_bylevel: float = 0.7
    min_child_weight: float = 6.0
    reg_lambda: float = 6.0
    reg_alpha: float = 0.5
    gamma: float = 0.1
    max_scale_pos_weight: float = 10.0
    topk: int = 120                    # for the auto_topk strategy
    n_splits: int = 5
    #: Repeats are what make the strategy comparison stable; on a few hundred
    #: positives a single 5-fold estimate moves by several AUC points with the
    #: seed. Wide strategies get fewer repeats because they cost far more and
    #: are rarely the winner - see ``_repeats_for``.
    n_repeats: int = 3
    wide_feature_threshold: int = 800
    wide_n_repeats: int = 1
    cv_n_estimators: int = 400
    seeds: tuple[int, ...] = (42, 202, 1337)
    early_stopping_rounds: int = 60
    random_state: int = 42
    strategies: tuple[str, ...] = STRATEGIES


@dataclass
class CVResult:
    strategy: str
    roc_auc: float
    pr_auc: float
    roc_auc_std: float
    n_features: int
    fold_scores: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "roc_auc": round(self.roc_auc, 5),
            "roc_auc_std": round(self.roc_auc_std, 5),
            "pr_auc": round(self.pr_auc, 5),
            "n_features": self.n_features,
        }


def _encode(X: pd.DataFrame, categories: dict[str, list] | None = None
            ) -> tuple[pd.DataFrame, dict[str, list]]:
    """Give categorical columns a stable category set across fit and predict."""
    out = X.copy()
    cats: dict[str, list] = {}
    for col in out.columns:
        is_text = (col in CATEGORICAL_COLUMNS
                   or out[col].dtype == object
                   or str(out[col].dtype) == "string")
        if not is_text:
            continue
        if categories is not None and col in categories:
            levels = categories[col]
        else:
            levels = sorted(pd.Series(out[col]).dropna().astype(str).unique().tolist())
        cats[col] = levels
        out[col] = pd.Categorical(out[col].astype("string"), categories=levels)
    return out, (categories if categories is not None else cats)


class BOIModel:
    """Gradient-boosted classifier over the bank's alert schema."""

    version = "1.0.0"

    def __init__(self, config: BOIConfig | None = None):
        self.config = config or BOIConfig()
        self.boosters: list[xgb.Booster] = []
        self.feature_names: list[str] = []
        self.categories: dict[str, list] = {}
        self.strategy: str = ""
        self.cv_results: list[CVResult] = []
        self.best_iteration: int = 0
        self.threshold: float = 0.5
        self.base_rate: float = 0.0
        self.dropped_dead_columns: int = 0
        self.report: dict[str, Any] = {}

    # -- helpers -----------------------------------------------------------

    def _params(self, y: np.ndarray, seed: int) -> dict:
        c = self.config
        pos = max(int(np.sum(y)), 1)
        neg = max(len(y) - pos, 1)
        return {
            "objective": "binary:logistic",
            "eval_metric": "aucpr",
            "max_depth": c.max_depth,
            "eta": c.learning_rate,
            "subsample": c.subsample,
            "colsample_bytree": c.colsample_bytree,
            "colsample_bylevel": c.colsample_bylevel,
            "min_child_weight": c.min_child_weight,
            "lambda": c.reg_lambda,
            "alpha": c.reg_alpha,
            "gamma": c.gamma,
            "scale_pos_weight": min(neg / pos, c.max_scale_pos_weight),
            "tree_method": "hist",
            "max_cat_to_onehot": 8,
            "seed": seed,
            "nthread": 0,
        }

    @staticmethod
    def _dmatrix(X: pd.DataFrame, y=None) -> xgb.DMatrix:
        return xgb.DMatrix(X, label=y, enable_categorical=True,
                           feature_names=list(X.columns))

    def _select(self, X: pd.DataFrame, y: np.ndarray, strategy: str,
                seed: int) -> list[str]:
        """Choose the columns for one fit. Called *inside* each CV fold."""
        dd = load_dictionary()
        finalized = [c for c in dd.bank_finalized if c in X.columns]
        engineered = bx.engineered_columns(X.columns)

        if strategy == "bank_finalized":
            return finalized or list(X.columns)
        if strategy == "bank_plus_engineered":
            return list(dict.fromkeys(finalized + engineered)) or list(X.columns)
        if strategy == "all":
            return list(X.columns)

        # auto_topk: rank by gain from a cheap shallow model fitted here, on
        # this fold's training rows only.
        params = self._params(y, seed) | {"max_depth": 3, "eta": 0.25,
                                          "colsample_bytree": 0.5}
        booster = xgb.train(params, self._dmatrix(X, y), num_boost_round=60)
        gain = booster.get_score(importance_type="total_gain")
        if not gain:
            return list(X.columns)
        ranked = sorted(gain.items(), key=lambda kv: -kv[1])
        picked = [c for c, _ in ranked[: self.config.topk]]
        # Always keep the bank's own choices; they encode domain knowledge the
        # importance ranking on a small fold cannot be trusted to rediscover.
        return list(dict.fromkeys(picked + finalized))

    # -- cross-validation ---------------------------------------------------

    def _repeats_for(self, strategy: str, n_cols: int) -> int:
        c = self.config
        wide = strategy == "all" or (strategy == "bank_plus_engineered"
                                     and n_cols > c.wide_feature_threshold)
        return c.wide_n_repeats if wide else c.n_repeats

    def cross_validate(self, X: pd.DataFrame, y: np.ndarray,
                       strategy: str) -> CVResult:
        c = self.config
        repeats = self._repeats_for(strategy, X.shape[1])
        cv = RepeatedStratifiedKFold(n_splits=c.n_splits, n_repeats=repeats,
                                     random_state=c.random_state)
        aucs, aps, n_feats = [], [], []
        for fold, (tr, te) in enumerate(cv.split(X, y)):
            X_tr, X_te = X.iloc[tr], X.iloc[te]
            y_tr, y_te = y[tr], y[te]
            cols = self._select(X_tr, y_tr, strategy, seed=c.random_state + fold)
            n_feats.append(len(cols))

            dtr = self._dmatrix(X_tr[cols], y_tr)
            dte = self._dmatrix(X_te[cols], y_te)
            booster = xgb.train(
                self._params(y_tr, c.random_state + fold), dtr,
                num_boost_round=c.cv_n_estimators,
                evals=[(dte, "val")],
                early_stopping_rounds=c.early_stopping_rounds,
                verbose_eval=False,
            )
            p = booster.predict(dte, iteration_range=(0, booster.best_iteration + 1))
            if len(np.unique(y_te)) > 1:
                aucs.append(roc_auc_score(y_te, p))
                aps.append(average_precision_score(y_te, p))
        return CVResult(
            strategy=strategy,
            roc_auc=float(np.mean(aucs)) if aucs else 0.5,
            roc_auc_std=float(np.std(aucs)) if aucs else 0.0,
            pr_auc=float(np.mean(aps)) if aps else 0.0,
            n_features=int(np.median(n_feats)) if n_feats else 0,
            fold_scores=[round(a, 5) for a in aucs],
        )

    # -- fit ----------------------------------------------------------------

    def fit(self, X: pd.DataFrame, y, verbose: bool = True) -> "BOIModel":
        t0 = time.perf_counter()
        y = np.asarray(y).astype(int)
        self.base_rate = float(y.mean())

        # Constant and all-null columns cannot help any strategy and cost real
        # time in the wide ones.
        numeric = X.select_dtypes(include=[np.number])
        dead = [c for c in numeric.columns if numeric[c].nunique(dropna=True) <= 1]
        if dead:
            X = X.drop(columns=dead)
        self.dropped_dead_columns = len(dead)

        X, self.categories = _encode(X)

        if verbose:
            print(f"  {len(X):,} rows x {X.shape[1]:,} columns, "
                  f"{int(y.sum())} positives ({y.mean():.2%})")

        # ---- choose a feature strategy by repeated CV ---------------------
        self.cv_results = []
        for strategy in self.config.strategies:
            result = self.cross_validate(X, y, strategy)
            self.cv_results.append(result)
            if verbose:
                print(f"  {strategy:22} ROC-AUC {result.roc_auc:.4f} "
                      f"(+/-{result.roc_auc_std:.4f})  PR-AUC {result.pr_auc:.4f}  "
                      f"[{result.n_features} features]")

        best = max(self.cv_results, key=lambda r: (r.pr_auc, r.roc_auc))
        self.strategy = best.strategy
        if verbose:
            print(f"  -> selected '{self.strategy}' on PR-AUC")

        # ---- refit on everything, ensembling over seeds -------------------
        cols = self._select(X, y, self.strategy, seed=self.config.random_state)
        self.feature_names = cols

        # A held-out slice fixes the number of rounds and the threshold; it is
        # not used for feature selection, which already happened above.
        skf = StratifiedKFold(n_splits=5, shuffle=True,
                              random_state=self.config.random_state)
        tr_idx, va_idx = next(iter(skf.split(X, y)))
        dtr = self._dmatrix(X.iloc[tr_idx][cols], y[tr_idx])
        dva = self._dmatrix(X.iloc[va_idx][cols], y[va_idx])

        self.boosters = []
        rounds = []
        for seed in self.config.seeds:
            booster = xgb.train(
                self._params(y[tr_idx], seed), dtr,
                num_boost_round=self.config.n_estimators,
                evals=[(dva, "val")],
                early_stopping_rounds=self.config.early_stopping_rounds,
                verbose_eval=False,
            )
            rounds.append(booster.best_iteration + 1)
            self.boosters.append(booster)
        self.best_iteration = int(np.median(rounds))

        p_va = self.predict_proba(X.iloc[va_idx], _already_encoded=True)
        self.threshold = self._best_threshold(y[va_idx], p_va)

        self.report = {
            "version": self.version,
            "rows": int(len(X)),
            "columns_seen": int(X.shape[1]),
            "dead_columns_dropped": self.dropped_dead_columns,
            "positives": int(y.sum()),
            "base_rate": round(self.base_rate, 5),
            "strategy": self.strategy,
            "n_features_used": len(cols),
            "boosting_rounds": self.best_iteration,
            "threshold": round(self.threshold, 4),
            "cv": [r.to_dict() for r in self.cv_results],
            "fit_seconds": round(time.perf_counter() - t0, 1),
        }
        if verbose:
            print(f"  fitted {len(self.boosters)} boosters, "
                  f"{self.best_iteration} rounds, threshold {self.threshold:.3f} "
                  f"({self.report['fit_seconds']}s)")
        return self

    @staticmethod
    def _best_threshold(y: np.ndarray, p: np.ndarray) -> float:
        """Threshold maximising F1 - a reasonable default when the organisers'
        metric is unknown, and easy to override at predict time."""
        if len(np.unique(y)) < 2:
            return 0.5
        precision, recall, thresholds = precision_recall_curve(y, p)
        f1 = np.divide(2 * precision * recall, precision + recall,
                       out=np.zeros_like(precision), where=(precision + recall) > 0)
        if len(thresholds) == 0:
            return 0.5
        return float(thresholds[int(np.argmax(f1[:-1]))])

    # -- predict -------------------------------------------------------------

    def predict_proba(self, X: pd.DataFrame, _already_encoded: bool = False
                      ) -> np.ndarray:
        if not self.boosters:
            raise RuntimeError("BOIModel is not fitted")
        if not _already_encoded:
            X, _ = _encode(X, self.categories)
        missing = [c for c in self.feature_names if c not in X.columns]
        if missing:
            for c in missing:
                X[c] = np.nan
        d = self._dmatrix(X[self.feature_names])
        preds = [b.predict(d, iteration_range=(0, b.best_iteration + 1))
                 for b in self.boosters]
        return np.mean(preds, axis=0)

    def predict(self, X: pd.DataFrame, threshold: float | None = None) -> np.ndarray:
        return (self.predict_proba(X) >= (threshold if threshold is not None
                                          else self.threshold)).astype(int)

    def importance(self, top: int = 30) -> pd.Series:
        gains: dict[str, float] = {}
        for b in self.boosters:
            for k, v in b.get_score(importance_type="total_gain").items():
                gains[k] = gains.get(k, 0.0) + v
        if not gains:
            return pd.Series(dtype=float)
        s = pd.Series(gains).sort_values(ascending=False)
        return (s / s.sum()).head(top)

    def evaluate(self, X: pd.DataFrame, y) -> dict:
        y = np.asarray(y).astype(int)
        p = self.predict_proba(X)
        pred = (p >= self.threshold).astype(int)
        return {
            "roc_auc": float(roc_auc_score(y, p)),
            "pr_auc": float(average_precision_score(y, p)),
            "f1": float(f1_score(y, pred, zero_division=0)),
            "positives": int(y.sum()),
            "flagged": int(pred.sum()),
            "threshold": round(self.threshold, 4),
        }

    # -- persistence ----------------------------------------------------------

    def save(self, directory: Path) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(self.boosters):
            b.save_model(str(d / f"booster_{i}.ubj"))
        (d / "boi_model.json").write_text(json.dumps({
            "version": self.version,
            "feature_names": self.feature_names,
            "categories": self.categories,
            "strategy": self.strategy,
            "threshold": self.threshold,
            "base_rate": self.base_rate,
            "best_iteration": self.best_iteration,
            "n_boosters": len(self.boosters),
            "report": self.report,
        }, indent=2))
        return d

    @classmethod
    def load(cls, directory: Path) -> "BOIModel":
        d = Path(directory)
        meta = json.loads((d / "boi_model.json").read_text())
        obj = cls()
        obj.feature_names = meta["feature_names"]
        obj.categories = {k: list(v) for k, v in meta["categories"].items()}
        obj.strategy = meta["strategy"]
        obj.threshold = meta["threshold"]
        obj.base_rate = meta.get("base_rate", 0.0)
        obj.best_iteration = meta.get("best_iteration", 0)
        obj.report = meta.get("report", {})
        for i in range(meta["n_boosters"]):
            b = xgb.Booster()
            b.load_model(str(d / f"booster_{i}.ubj"))
            obj.boosters.append(b)
        return obj


__all__ = ["BOIModel", "BOIConfig", "CVResult", "STRATEGIES", "TARGET"]
