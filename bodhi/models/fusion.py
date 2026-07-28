"""Layer 7 - fuse the tabular, structural, temporal and intelligence scores.

The four layers see genuinely different things, and a single number has to come
out the other end. Two properties are non-negotiable, and they drive the whole
design of this file:

* **Monotonicity.** The blend weights are constrained to be non-negative, so
  the fused score can never *fall* when a layer's evidence rises. Without that
  constraint a stacker fitted on a small fold happily learns "high temporal
  score means safer", which is indefensible to an investigator and unshippable
  past a regulator.
* **Calibration.** The output drives an automated kill-switch at 85, so the
  number must mean something. Isotonic regression maps the blend onto observed
  frequencies; Brier score and expected calibration error are reported in the
  evaluation.

Fusion is fitted on its own fold, which no base model has seen - not on the
validation fold that early-stopped them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.isotonic import IsotonicRegression

from bodhi.config import FusionConfig, MODELS

LAYERS: tuple[str, ...] = ("xgb", "graph", "temporal", "intel")

STACK_FEATURES: tuple[str, ...] = (*LAYERS, "max_logit")

_EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def _stack(scores: dict[str, np.ndarray] | pd.DataFrame) -> np.ndarray:
    """Assemble the meta-feature matrix from the four sub-scores.

    Two deliberate choices:

    **Log-odds, not probabilities.** Sub-scores pile up against 0 and 1, where
    a linear model in probability space cannot tell 0.990 from 0.9999 - yet
    that is a hundredfold difference in odds, and it is exactly the region
    where alert ranking is decided.

    **Five features, not fifteen.** The fusion fold is the only fold no base
    model has seen, so it is small and carries few positives. Every extra
    meta-feature is another chance to fit noise; ``max_logit`` earns its place
    because "one layer is extremely confident and the others are quiet" is a
    real and different situation from "everything is mildly elevated".
    """
    cols = [np.asarray(scores[name], dtype=float).ravel() for name in LAYERS]
    S = np.clip(np.column_stack(cols), 0.0, 1.0)
    Z = _logit(S)
    return np.column_stack([Z, Z.max(axis=1)])


class RiskFusion:
    """Calibrated logistic stacker over the four layer scores."""

    def __init__(self, config: FusionConfig | None = None):
        self.config = config or MODELS.fusion
        self.weights: np.ndarray | None = None
        self.intercept: float = 0.0
        self.scaler: tuple[np.ndarray, np.ndarray] = (np.zeros(1), np.ones(1))
        self.calibrator: IsotonicRegression | None = None
        self.fitted = False

    # -- training ---------------------------------------------------------

    def fit(self, scores: dict[str, np.ndarray], y: np.ndarray,
            calibrate: bool = True) -> "RiskFusion":
        """Fit non-negative blend weights, then calibrate.

        The weights are constrained to be **non-negative**. This is not a
        regularisation trick, it is a requirement: with w >= 0 the fused score
        is monotone non-decreasing in every layer, so a confirmed NCRP ticket
        or a higher graph score can never make an account look *safer*. An
        unconstrained stacker on a fold this small routinely learns a negative
        coefficient on the temporal layer, which is indefensible in front of an
        investigator and unshippable in front of a regulator.

        A side effect is that the blend can always fall back to "all weight on
        the single best layer", so fusion cannot do much worse than its best
        input - which an unconstrained stacker demonstrably could.
        """
        S = _stack(scores)
        y = np.asarray(y).astype(int)
        n_features = S.shape[1]

        pos = max(int(y.sum()), 1)
        neg = max(len(y) - pos, 1)
        sample_w = np.where(y > 0, neg / pos, 1.0)

        mu = S.mean(axis=0)
        sigma = S.std(axis=0) + 1e-6
        Sz = (S - mu) / sigma

        def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
            w, b = theta[:-1], theta[-1]
            z = Sz @ w + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))
            eps = 1e-12
            loss = -(sample_w * (y * np.log(p + eps) +
                                 (1 - y) * np.log(1 - p + eps))).sum() / sample_w.sum()
            g = (sample_w * (p - y)) / sample_w.sum()
            grad = np.concatenate([Sz.T @ g, [g.sum()]])
            # light ridge to keep the blend from chasing one noisy fold
            loss += 0.02 * float(w @ w)
            grad[:-1] += 0.04 * w
            return loss, grad

        bounds = [(0.0, None)] * n_features + [(None, None)]
        theta0 = np.concatenate([np.full(n_features, 0.5), [0.0]])
        res = minimize(objective, theta0, jac=True, method="L-BFGS-B",
                       bounds=bounds, options={"maxiter": 500})

        self.weights = np.maximum(res.x[:-1], 0.0)
        self.intercept = float(res.x[-1])
        self.scaler = (mu, sigma)
        if not np.any(self.weights > 0):  # degenerate fold: fall back to priors
            self.weights = None

        if calibrate and self.weights is not None and len(np.unique(y)) > 1:
            raw = self._raw_proba(S)
            self.calibrator = IsotonicRegression(out_of_bounds="clip",
                                                 y_min=0.0, y_max=1.0)
            self.calibrator.fit(raw, y)
        self.fitted = self.weights is not None
        return self

    def _raw_proba(self, S: np.ndarray) -> np.ndarray:
        mu, sigma = self.scaler
        z = ((S - mu) / sigma) @ self.weights + self.intercept
        return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))

    @property
    def layer_weights(self) -> dict[str, float]:
        """Fitted blend weight per meta-feature, for the model card."""
        if self.weights is None:
            return dict(self.config.prior_weights)
        return {name: round(float(w), 4)
                for name, w in zip(STACK_FEATURES, self.weights)}

    # -- inference ---------------------------------------------------------

    #: Weight of the raw stacker output retained after isotonic calibration.
    #: Isotonic regression is a step function: on a near-separable problem it
    #: collapses thousands of distinct scores into a handful of plateaus, and
    #: every account inside a plateau becomes tied. Ties destroy the ranking an
    #: alert queue depends on - measured as a 4-point AUC drop when this was
    #: zero. Retaining a sliver of the (monotone) raw score breaks the ties
    #: without measurably disturbing calibration.
    TIE_BREAK = 0.02

    def predict_proba(self, scores: dict[str, np.ndarray]) -> np.ndarray:
        S = _stack(scores)
        if not self.fitted or self.weights is None:
            return self._prior_blend(scores)
        raw = self._raw_proba(S)
        if self.calibrator is None:
            return np.clip(raw, 0.0, 1.0)
        cal = self.calibrator.predict(raw)
        p = (1.0 - self.TIE_BREAK) * cal + self.TIE_BREAK * raw
        return np.clip(p, 0.0, 1.0)

    def _prior_blend(self, scores: dict[str, np.ndarray]) -> np.ndarray:
        """Fallback used before the stacker has ever been fitted."""
        w = self.config.prior_weights
        total = sum(w.values())
        out = np.zeros(len(np.asarray(scores[LAYERS[0]]).ravel()))
        for name in LAYERS:
            out += w.get(name, 0.0) * np.clip(np.asarray(scores[name], dtype=float), 0, 1)
        return out / total

    def risk_score(self, scores: dict[str, np.ndarray]) -> np.ndarray:
        """Probability mapped onto the 0-100 supervisory scale.

        A square-root warp spreads the crowded low-probability region out so
        that a 1-in-500 account and a 1-in-50 account do not both render as
        "2". The mapping is monotone, so ranking is untouched.
        """
        p = self.predict_proba(scores)
        return np.clip(100.0 * np.sqrt(np.clip(p, 0.0, 1.0)), 0.0, 100.0)

    def contributions(self, scores: dict[str, np.ndarray]) -> pd.DataFrame:
        """Per-layer contribution to the fused log-odds, for the audit trail."""
        S = _stack(scores)
        if not self.fitted or self.weights is None:
            w = self.config.prior_weights
            return pd.DataFrame({name: np.asarray(scores[name], dtype=float) *
                                 w.get(name, 0.0) for name in LAYERS})
        mu, sigma = self.scaler
        Sz = (S - mu) / sigma
        return pd.DataFrame({name: Sz[:, i] * self.weights[i]
                             for i, name in enumerate(STACK_FEATURES)})

    # -- persistence --------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict = {
            "fitted": self.fitted,
            "weights": self.weights.tolist() if self.weights is not None else None,
            "intercept": self.intercept,
            "scaler_mu": np.asarray(self.scaler[0]).tolist(),
            "scaler_sigma": np.asarray(self.scaler[1]).tolist(),
            "stack_features": list(STACK_FEATURES),
        }
        if self.calibrator is not None:
            payload["calib_x"] = np.asarray(self.calibrator.X_thresholds_).tolist()
            payload["calib_y"] = np.asarray(self.calibrator.y_thresholds_).tolist()
        path.with_suffix(".json").write_text(json.dumps(payload))

    @classmethod
    def load(cls, path: Path) -> "RiskFusion":
        payload = json.loads(Path(path).with_suffix(".json").read_text())
        obj = cls()
        if payload.get("weights") is not None:
            obj.weights = np.array(payload["weights"], dtype=float)
        obj.intercept = float(payload.get("intercept", 0.0))
        obj.scaler = (np.array(payload.get("scaler_mu", [0.0])),
                      np.array(payload.get("scaler_sigma", [1.0])))
        if payload.get("calib_x") is not None:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.X_thresholds_ = np.array(payload["calib_x"])
            iso.y_thresholds_ = np.array(payload["calib_y"])
            iso.X_min_ = float(iso.X_thresholds_.min())
            iso.X_max_ = float(iso.X_thresholds_.max())
            iso.y_min, iso.y_max = 0.0, 1.0
            iso.increasing_ = True
            from scipy.interpolate import interp1d
            iso.f_ = interp1d(iso.X_thresholds_, iso.y_thresholds_, kind="linear",
                              bounds_error=False,
                              fill_value=(iso.y_thresholds_[0], iso.y_thresholds_[-1]))
            obj.calibrator = iso
        obj.fitted = bool(payload.get("fitted", False)) and obj.weights is not None
        return obj


__all__ = ["RiskFusion", "LAYERS", "STACK_FEATURES"]
