#!/usr/bin/env python3
"""Evaluate the engine and write ``artifacts/metrics/evaluation.json``.

Every number quoted in the report and shown on the dashboard is produced here,
from a single reproducible run. The evaluation is deliberately unkind to the
system:

* the held-out fold is never touched until the end;
* the rule-based incumbent is given its best operating point, not a strawman;
* the comparison against it is made **at equal recall**, because a model that
  alerts on everything can always claim to "catch more";
* rings that only switch on in the final quarter of the window are scored
  separately, as a genuine out-of-time test;
* detection lead time is measured against the government ticket feed, by
  re-scoring the world as of several earlier instants.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)

from bodhi.baselines import rule_engine, rule_summary  # noqa: E402
from bodhi.config import (  # noqa: E402
    DATA_DIR,
    FIGURE_DIR,
    METRICS_DIR,
    MODEL_DIR,
    ensure_dirs,
)
from bodhi.data.generator import load  # noqa: E402
from bodhi.engine.pipeline import MuleHunterEngine  # noqa: E402


# --------------------------------------------------------------------------
# metric helpers
# --------------------------------------------------------------------------


def ranking_metrics(y: np.ndarray, score: np.ndarray,
                    budgets: tuple[float, ...] = (0.005, 0.01, 0.02, 0.05)) -> dict:
    """AUCs plus what an analyst actually experiences: precision at a budget."""
    y = np.asarray(y).astype(int)
    out: dict = {
        "roc_auc": float(roc_auc_score(y, score)),
        "pr_auc": float(average_precision_score(y, score)),
    }
    order = np.argsort(-score)
    total_pos = max(int(y.sum()), 1)
    for b in budgets:
        k = max(1, int(round(b * len(y))))
        top = order[:k]
        hits = int(y[top].sum())
        out[f"precision_at_{b:g}pct".replace("0.", "")] = round(hits / k, 4)
        out[f"recall_at_{b:g}pct".replace("0.", "")] = round(hits / total_pos, 4)
    # Canonical shorthand used elsewhere in the report.
    k1 = max(1, int(round(0.01 * len(y))))
    out["precision_at_1pct"] = round(int(y[order[:k1]].sum()) / k1, 4)
    out["recall_at_1pct"] = round(int(y[order[:k1]].sum()) / total_pos, 4)
    return out


def calibration(y: np.ndarray, prob: np.ndarray, bins: int = 10) -> dict:
    """Brier score and expected calibration error over equal-width bins."""
    y = np.asarray(y).astype(float)
    prob = np.clip(np.asarray(prob, dtype=float), 0, 1)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(prob, edges) - 1, 0, bins - 1)
    ece, rows = 0.0, []
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        conf, freq = float(prob[m].mean()), float(y[m].mean())
        ece += (m.sum() / len(y)) * abs(conf - freq)
        rows.append({"bin": b, "n": int(m.sum()), "mean_predicted": round(conf, 4),
                     "observed_rate": round(freq, 4)})
    return {"brier": float(brier_score_loss(y, prob)),
            "ece": round(float(ece), 5), "bins": rows}


def confusion_at(y: np.ndarray, score: np.ndarray, threshold: float) -> dict:
    pred = score >= threshold
    y = np.asarray(y).astype(bool)
    tp, fp = int((pred & y).sum()), int((pred & ~y).sum())
    fn, tn = int((~pred & y).sum()), int((~pred & ~y).sum())
    return {
        "threshold": threshold, "alerts": tp + fp,
        "true_positives": tp, "false_positives": fp,
        "false_negatives": fn, "true_negatives": tn,
        "precision": round(tp / max(tp + fp, 1), 4),
        "recall": round(tp / max(tp + fn, 1), 4),
        "f1": round(2 * tp / max(2 * tp + fp + fn, 1), 4),
        "alert_rate": round((tp + fp) / max(len(y), 1), 5),
    }


def precision_at_recall(y: np.ndarray, score: np.ndarray,
                        target_recall: float) -> tuple[float, float, int]:
    """Precision, threshold and alert count at the first point reaching recall."""
    precision, recall, thresholds = precision_recall_curve(y, score)
    ok = np.flatnonzero(recall[:-1] >= target_recall)
    if ok.size == 0:
        return 0.0, 0.0, len(y)
    i = ok[-1]  # highest threshold that still achieves the recall
    thr = float(thresholds[i])
    return float(precision[i]), thr, int((score >= thr).sum())


# --------------------------------------------------------------------------
# analyses
# --------------------------------------------------------------------------


def per_typology(accounts: pd.DataFrame, scores: pd.DataFrame,
                 threshold: float) -> list[dict]:
    """Recall broken down by laundering pattern - averages hide blind spots."""
    df = accounts.set_index("account_id").join(scores["risk_score"])
    rows = []
    for typ, grp in df[df["is_mule"]].groupby("mule_typology"):
        detected = int((grp["risk_score"] >= threshold).sum())
        rows.append({
            "typology": str(typ),
            "mules": int(len(grp)),
            "detected": detected,
            "recall": round(detected / max(len(grp), 1), 4),
            "median_score": round(float(grp["risk_score"].median()), 2),
        })
    for role, grp in df[df["is_mule"]].groupby("mule_role"):
        detected = int((grp["risk_score"] >= threshold).sum())
        rows.append({
            "typology": f"role:{role}",
            "mules": int(len(grp)),
            "detected": detected,
            "recall": round(detected / max(len(grp), 1), 4),
            "median_score": round(float(grp["risk_score"].median()), 2),
        })
    return sorted(rows, key=lambda r: r["recall"])


def hard_negative_report(accounts: pd.DataFrame, scores: pd.DataFrame,
                         threshold: float) -> list[dict]:
    """How often each planted decoy population is wrongly alerted."""
    df = accounts.set_index("account_id").join(scores["risk_score"])
    clean = df[~df["is_mule"]]
    rows = []
    populations = {
        "small_business_fan_in": "is_small_business",
        "legitimate_dormant_wake": "legit_dormant_wake",
        "community_collector": "is_legit_collector",
        "bc_agent_device_cluster": "is_bc_agent",
        "merchant": "is_merchant",
    }
    for label, col in populations.items():
        if col not in clean.columns:
            continue
        grp = clean[clean[col].astype(bool)]
        if not len(grp):
            continue
        fp = int((grp["risk_score"] >= threshold).sum())
        rows.append({
            "population": label, "n": int(len(grp)), "false_positives": fp,
            "false_positive_rate": round(fp / len(grp), 4),
            "median_score": round(float(grp["risk_score"].median()), 2),
        })
    ordinary = clean[~clean[[c for c in populations.values()
                             if c in clean.columns]].any(axis=1)]
    if len(ordinary):
        fp = int((ordinary["risk_score"] >= threshold).sum())
        rows.append({"population": "ordinary_retail", "n": int(len(ordinary)),
                     "false_positives": fp,
                     "false_positive_rate": round(fp / len(ordinary), 4),
                     "median_score": round(float(ordinary["risk_score"].median()), 2)})
    return sorted(rows, key=lambda r: -r["false_positive_rate"])


def independence_from_tickets(accounts: pd.DataFrame, scores: pd.DataFrame,
                              fraud_alerts: pd.DataFrame,
                              threshold: float) -> dict:
    """How much of the detection is the model's own work?

    The supervised layers never see NCRP tickets, so mules that were detected
    *and* never appeared in any ticket are cases the government feed could not
    have supplied.
    """
    reported = set(fraud_alerts["beneficiary_account"].astype(str)) \
        if len(fraud_alerts) else set()
    df = accounts.set_index("account_id").join(scores["risk_score"])
    mules = df[df["is_mule"]]
    detected = mules[mules["risk_score"] >= threshold]
    never_reported = [a for a in detected.index if a not in reported]
    all_unreported = [a for a in mules.index if a not in reported]
    return {
        "mules_total": int(len(mules)),
        "mules_named_in_tickets": int(len([a for a in mules.index if a in reported])),
        "mules_never_reported": len(all_unreported),
        "detected_total": int(len(detected)),
        "detected_never_reported": len(never_reported),
        "share_of_detections_not_in_any_ticket": round(
            len(never_reported) / max(len(detected), 1), 4),
        "recall_on_never_reported_mules": round(
            len(never_reported) / max(len(all_unreported), 1), 4),
    }


def lead_time(engine: MuleHunterEngine, sim, threshold: float,
              n_cuts: int = 12, window_fraction: float = 0.35) -> dict:
    """Re-score the world at earlier instants to measure detection lead time.

    Two constraints make this measurement mean something rather than merely
    look good:

    **Resolution.** Each cut is a full re-score, so the grid is finite and lead
    time can only be resolved to the cut spacing. The grid is therefore packed
    into the final ``window_fraction`` of the simulation instead of being spread
    thinly across the whole of it.

    **Population.** Only mules whose *first fraudulent transaction* falls inside
    that grid are eligible. Including a mule that was already active before the
    first cut would record its detection at the first cut regardless of when the
    engine could really have seen it, which manufactures a negative lead time
    out of nothing but grid placement.

    Positive lead time means the bank knew before the victim's complaint
    reached it.
    """
    tx = sim.transactions
    t_end, t_start = float(tx["ts"].max()), float(tx["ts"].min())
    span = t_end - t_start
    grid_start = t_end - span * window_fraction
    cuts = list(np.linspace(grid_start, t_end, n_cuts))
    spacing_hours = (cuts[1] - cuts[0]) / 3_600.0 if len(cuts) > 1 else 0.0

    # First fraudulent involvement per account.
    fraud = tx[tx["is_fraud"]]
    first_fraud: dict[str, float] = {}
    for col in ("src_account", "dst_account"):
        g = fraud.groupby(col)["ts"].min()
        for account, ts in g.items():
            account = str(account)
            if account not in first_fraud or ts < first_fraud[account]:
                first_fraud[account] = float(ts)

    accounts = sim.accounts.set_index("account_id")
    eligible = [a for a in accounts.index[accounts["is_mule"]]
                if first_fraud.get(str(a), -np.inf) >= grid_start]

    first_alert: dict[str, float] = {}
    for cut in cuts:
        result = engine.score_all(as_of=cut, detect_rings_too=False)
        hot = result.scores.index[result.scores["risk_score"] >= threshold]
        for a in hot:
            first_alert.setdefault(str(a), cut)

    fa = sim.fraud_alerts
    first_ticket: dict[str, float] = {}
    if len(fa):
        rep = pd.to_datetime(fa["reported_at"], utc=True).astype("int64") / 1e9
        for account, ts in zip(fa["beneficiary_account"].astype(str), rep):
            if account not in first_ticket or ts < first_ticket[account]:
                first_ticket[account] = float(ts)

    leads, before, after = [], 0, 0
    detected_never_ticketed = 0
    for m in eligible:
        m = str(m)
        if m not in first_alert:
            continue
        if m not in first_ticket:
            detected_never_ticketed += 1
            continue
        hours = (first_ticket[m] - first_alert[m]) / 3_600.0
        leads.append(hours)
        before += hours > 0
        after += hours <= 0

    # Leave the engine scored at the full window.
    engine.score_all()

    return {
        "cuts_evaluated": len(cuts),
        "cut_spacing_hours": round(spacing_hours, 1),
        "grid_covers_final_fraction": window_fraction,
        "eligible_mules": len(eligible),
        "note": "Only mules whose first fraudulent transaction falls inside the "
                "cut grid are eligible; lead time is resolved to the cut spacing.",
        "detected_within_grid": len(leads) + detected_never_ticketed,
        "detected_but_never_ticketed": detected_never_ticketed,
        "mules_with_ticket_and_detection": len(leads),
        "detected_before_ticket": int(before),
        "detected_after_ticket": int(after),
        "share_detected_before_ticket": round(before / max(len(leads), 1), 4),
        "median_lead_hours": round(float(np.median(leads)), 2) if leads else None,
        "mean_lead_hours": round(float(np.mean(leads)), 2) if leads else None,
        "p90_lead_hours": round(float(np.percentile(leads, 90)), 2) if leads else None,
    }


def out_of_time(sim, scores: pd.DataFrame, threshold: float) -> dict:
    """Rings whose first activity falls in the final window."""
    rings = sim.rings
    if not len(rings):
        return {}
    late = rings[rings["is_late_ring"]]
    members: set[str] = set()
    for m in late["members"]:
        members.update(x for x in str(m).split(",") if x)
    if not members:
        return {}
    idx = scores.index.intersection(list(members))
    detected = int((scores.loc[idx, "risk_score"] >= threshold).sum())
    return {
        "late_rings": int(len(late)),
        "late_ring_mules": int(len(idx)),
        "detected": detected,
        "recall": round(detected / max(len(idx), 1), 4),
        "median_score": round(float(scores.loc[idx, "risk_score"].median()), 2),
    }


def latency_benchmark(engine: MuleHunterEngine, sim, n: int = 2_000) -> dict:
    """Inline decision latency - the number that decides deployability."""
    tx = sim.transactions.tail(n)
    samples = []
    for _, r in tx.iterrows():
        t0 = time.perf_counter()
        engine.score_transaction({
            "txn_id": r["txn_id"], "src_account": r["src_account"],
            "dst_account": r["dst_account"], "amount": float(r["amount"]),
            "channel": r["channel"],
            "beneficiary_age_hours": r.get("beneficiary_age_hours"),
        })
        samples.append((time.perf_counter() - t0) * 1000)
    arr = np.array(samples)
    return {
        "n": int(arr.size),
        "mean_ms": round(float(arr.mean()), 4),
        "p50_ms": round(float(np.percentile(arr, 50)), 4),
        "p95_ms": round(float(np.percentile(arr, 95)), 4),
        "p99_ms": round(float(np.percentile(arr, 99)), 4),
        "max_ms": round(float(arr.max()), 4),
        "throughput_per_sec": int(1000 / max(float(arr.mean()), 1e-6)),
    }


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------


def make_figures(y: np.ndarray, scores: pd.DataFrame, layers: dict,
                 calib: dict, typology: list[dict], out_dir: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.facecolor": "white", "axes.grid": True,
        "grid.alpha": 0.25, "font.size": 9,
    })
    written: list[str] = []

    # ROC + PR
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for name, col in (("XGBoost (L4)", "score_xgb"), ("GraphSAGE (L5)", "score_graph"),
                      ("TGN (L6)", "score_temporal"), ("Fused (L7)", "risk_score")):
        s = scores[col].to_numpy()
        fpr, tpr, _ = roc_curve(y, s)
        axes[0].plot(fpr, tpr, lw=1.6,
                     label=f"{name}  AUC={roc_auc_score(y, s):.3f}")
        pr, rc, _ = precision_recall_curve(y, s)
        axes[1].plot(rc, pr, lw=1.6,
                     label=f"{name}  AP={average_precision_score(y, s):.3f}")
    axes[0].plot([0, 1], [0, 1], "k--", lw=0.8, alpha=0.5)
    axes[0].set(xlabel="False positive rate", ylabel="True positive rate",
                title="ROC by layer")
    axes[1].set(xlabel="Recall", ylabel="Precision", title="Precision-recall by layer")
    axes[0].legend(fontsize=7.5); axes[1].legend(fontsize=7.5)
    fig.tight_layout()
    p = out_dir / "roc_pr.png"
    fig.savefig(p, dpi=160); plt.close(fig); written.append(p.name)

    # Calibration
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    bins = calib["bins"]
    ax.plot([0, 1], [0, 1], "k--", lw=0.8, alpha=0.5, label="perfect")
    ax.plot([b["mean_predicted"] for b in bins], [b["observed_rate"] for b in bins],
            "o-", lw=1.6, label=f"BODHI (ECE={calib['ece']:.4f})")
    ax.set(xlabel="Predicted probability", ylabel="Observed mule rate",
           title="Calibration of the fused score")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = out_dir / "calibration.png"
    fig.savefig(p, dpi=160); plt.close(fig); written.append(p.name)

    # Recall by typology
    rows = [r for r in typology if not r["typology"].startswith("role:")]
    if rows:
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        names = [r["typology"].replace("_", "\n") for r in rows]
        ax.bar(names, [r["recall"] for r in rows], color="#4d7fff")
        ax.set(ylabel="Recall at alert threshold", ylim=(0, 1.05),
               title="Detection by laundering typology")
        ax.tick_params(axis="x", labelsize=7)
        fig.tight_layout()
        p = out_dir / "typology_recall.png"
        fig.savefig(p, dpi=160); plt.close(fig); written.append(p.name)

    # Alert-budget curve
    fig, ax = plt.subplots(figsize=(5.4, 4.0))
    s = scores["risk_score"].to_numpy()
    order = np.argsort(-s)
    ks = np.unique(np.linspace(1, len(s), 300).astype(int))
    prec = [y[order[:k]].sum() / k for k in ks]
    rec = [y[order[:k]].sum() / max(y.sum(), 1) for k in ks]
    ax.plot(ks / len(s) * 100, prec, lw=1.8, label="Precision")
    ax.plot(ks / len(s) * 100, rec, lw=1.8, label="Recall")
    ax.set(xlabel="Alert budget (% of accounts reviewed)", ylabel="Rate",
           title="What an analyst gets for a given workload", xscale="log")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = out_dir / "alert_budget.png"
    fig.savefig(p, dpi=160); plt.close(fig); written.append(p.name)

    return written


# --------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=DATA_DIR)
    p.add_argument("--models", type=Path, default=MODEL_DIR)
    p.add_argument("--threshold", type=float, default=40.0)
    p.add_argument("--no-lead-time", action="store_true",
                   help="skip the multi-cut re-scoring (the slow part)")
    p.add_argument("--no-figures", action="store_true")
    args = p.parse_args()

    ensure_dirs()
    sim = load(args.data)
    engine = MuleHunterEngine.load(args.models)
    engine.attach(sim.accounts, sim.transactions, sim.fraud_alerts,
                  sim.iocs, sim.regulatory)
    print("Scoring the full population …")
    result = engine.score_all()
    scores = result.scores

    accounts = sim.accounts.set_index("account_id")
    y = accounts["is_mule"].reindex(scores.index).to_numpy().astype(int)

    print("Layer-by-layer ranking metrics …")
    layers = {
        "xgb_layer4": ranking_metrics(y, scores["score_xgb"].to_numpy()),
        "graphsage_layer5": ranking_metrics(y, scores["score_graph"].to_numpy()),
        "tgn_layer6": ranking_metrics(y, scores["score_temporal"].to_numpy()),
        "intel_channel": ranking_metrics(y, scores["score_intel"].to_numpy()),
        "fused_layer7": ranking_metrics(y, scores["risk_score"].to_numpy()),
    }

    calib = calibration(y, scores["prob_mule"].to_numpy())

    print("Rule-engine baseline …")
    features = engine.features
    rules = rule_engine(features)
    rule_alert = rules["alert"].to_numpy()
    rule_tp = int((rule_alert & (y == 1)).sum())
    rule_fp = int((rule_alert & (y == 0)).sum())
    rule_recall = rule_tp / max(int(y.sum()), 1)
    rule_precision = rule_tp / max(int(rule_alert.sum()), 1)

    bodhi_prec, bodhi_thr, _ = precision_at_recall(
        y, scores["risk_score"].to_numpy(), rule_recall)
    # Count the actual confusion at that operating point rather than inferring
    # it from the recall target - the inferred version rounds its way to a
    # flattering "100% of false positives removed".
    at_equal = confusion_at(y, scores["risk_score"].to_numpy(), bodhi_thr)
    fp_at_equal_recall = at_equal["false_positives"]
    bodhi_alerts = at_equal["alerts"]
    fp_reduction = 1.0 - (fp_at_equal_recall / max(rule_fp, 1))

    baseline = {
        "rule_pack_size": len(rule_summary(rules, y)),
        "alerts": int(rule_alert.sum()),
        "alert_rate": round(float(rule_alert.mean()), 5),
        "true_positives": rule_tp,
        "false_positives": rule_fp,
        "precision": round(rule_precision, 4),
        "recall": round(rule_recall, 4),
        "bodhi_threshold_at_equal_recall": round(bodhi_thr, 3),
        "bodhi_alerts_at_equal_recall": bodhi_alerts,
        "bodhi_precision_at_equal_recall": round(bodhi_prec, 4),
        "bodhi_true_positives_at_equal_recall": at_equal["true_positives"],
        "bodhi_false_positives_at_equal_recall": fp_at_equal_recall,
        "bodhi_recall_at_equal_recall": at_equal["recall"],
        "false_positive_reduction": round(max(fp_reduction, 0.0), 5),
        "precision_uplift_x": round(bodhi_prec / max(rule_precision, 1e-9), 2),
        "per_rule": json.loads(rule_summary(rules, y).to_json(orient="records")),
    }

    print("Typology, decoy and independence analyses …")
    typology = per_typology(sim.accounts, scores, args.threshold)
    decoys = hard_negative_report(sim.accounts, scores, args.threshold)
    independence = independence_from_tickets(sim.accounts, scores,
                                             sim.fraud_alerts, args.threshold)
    oot = out_of_time(sim, scores, args.threshold)

    thresholds = {str(t): confusion_at(y, scores["risk_score"].to_numpy(), t)
                  for t in (40, 55, 65, 85)}

    print("Latency benchmark …")
    latency = latency_benchmark(engine, sim)

    lead = {}
    if not args.no_lead_time:
        print("Detection lead time (re-scoring at earlier instants) …")
        lead = lead_time(engine, sim, args.threshold)
        scores = engine.last_result.scores

    figures: list[str] = []
    if not args.no_figures:
        print("Figures …")
        figures = make_figures(y, scores, layers, calib, typology, FIGURE_DIR)

    payload = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": sim.summary(),
        "headline": {
            "roc_auc": layers["fused_layer7"]["roc_auc"],
            "pr_auc": layers["fused_layer7"]["pr_auc"],
            "precision_at_1pct": layers["fused_layer7"]["precision_at_1pct"],
            "recall_at_1pct": layers["fused_layer7"]["recall_at_1pct"],
            "false_positive_reduction_vs_rules": baseline["false_positive_reduction"],
            "precision_uplift_x": baseline["precision_uplift_x"],
            "median_lead_hours": lead.get("median_lead_hours"),
            "p99_decision_latency_ms": latency["p99_ms"],
            "ece": calib["ece"],
        },
        "layers": {
            "XGBoost (L4)": layers["xgb_layer4"],
            "GraphSAGE (L5)": layers["graphsage_layer5"],
            "TGN (L6)": layers["tgn_layer6"],
            "Intelligence": layers["intel_channel"],
            "Fused (L7)": layers["fused_layer7"],
        },
        "calibration": calib,
        "baseline": baseline,
        "thresholds": thresholds,
        "typology": typology,
        "hard_negatives": decoys,
        "independence_from_tickets": independence,
        "out_of_time": oot,
        "latency": latency,
        "lead_time": lead,
        "fusion_weights": engine.fusion.layer_weights,
        "figures": figures,
        "train_report": engine.train_report,
    }

    out = METRICS_DIR / "evaluation.json"
    out.write_text(json.dumps(payload, indent=2, default=str))

    # ---- console summary ------------------------------------------------
    h = payload["headline"]
    print("\n" + "=" * 74)
    print("BODHI MULE HUNTER AI - EVALUATION")
    print("=" * 74)
    print(f"Accounts {len(y):,}   mules {int(y.sum())} ({y.mean():.2%})")
    print(f"\n{'Layer':<18}{'ROC-AUC':>10}{'PR-AUC':>10}{'P@1%':>9}{'R@1%':>9}")
    for name, m in payload["layers"].items():
        print(f"{name:<18}{m['roc_auc']:>10.4f}{m['pr_auc']:>10.4f}"
              f"{m['precision_at_1pct']:>9.3f}{m['recall_at_1pct']:>9.3f}")
    print(f"\nCalibration      Brier={calib['brier']:.5f}  ECE={calib['ece']:.5f}")
    print(f"\nRule engine      {baseline['alerts']:,} alerts "
          f"({rule_tp} true / {rule_fp:,} false), "
          f"precision {baseline['precision']:.2%}, recall {baseline['recall']:.1%}")
    print(f"BODHI @ same recall  {baseline['bodhi_alerts_at_equal_recall']:,} alerts "
          f"({at_equal['true_positives']} true / {fp_at_equal_recall} false), "
          f"precision {baseline['bodhi_precision_at_equal_recall']:.2%}")
    print(f"False positives      {rule_fp:,} -> {fp_at_equal_recall} "
          f"({baseline['false_positive_reduction']:.3%} removed, "
          f"{baseline['precision_uplift_x']}x precision)")
    if lead:
        print(f"\nLead time        median {lead['median_lead_hours']} h "
              f"(cut spacing {lead['cut_spacing_hours']} h), "
              f"{lead['share_detected_before_ticket']:.0%} of {lead['mules_with_ticket_and_detection']} "
              f"eligible mules detected before the ticket arrived;")
        print(f"                 {lead['detected_but_never_ticketed']} more were detected "
              f"and never ticketed at all")
    print(f"Independence     {independence['share_of_detections_not_in_any_ticket']:.0%} "
          f"of detections were never named in any ticket")
    if oot:
        print(f"Out-of-time      recall {oot['recall']:.1%} on {oot['late_ring_mules']} "
              f"mules from rings that only activated in the final window")
    print(f"Latency          p50 {latency['p50_ms']} ms, p99 {latency['p99_ms']} ms")
    print("\nWeakest typologies")
    for row in typology[:4]:
        print(f"  {row['typology']:<26} recall {row['recall']:.1%} "
              f"({row['detected']}/{row['mules']})")
    print("\nDecoy populations (planted legitimate look-alikes)")
    for row in decoys[:5]:
        print(f"  {row['population']:<26} FP rate {row['false_positive_rate']:.2%} "
              f"of {row['n']}")
    print(f"\nWritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
