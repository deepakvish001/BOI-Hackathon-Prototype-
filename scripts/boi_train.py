#!/usr/bin/env python3
"""Train the alert-dataset model on the organisers' file.

    python scripts/boi_train.py --train BOI_train.csv

Accepts csv / tsv / parquet / xlsx. Prints an alignment report first, because
the most likely failure on submission day is not the model - it is a column
that arrived with a different name, or a numeric column shipped as text.

By default the resolution-status columns are quarantined: they record how an
analyst closed the alert and are unavailable when scoring an open one. Pass
``--allow-leakage`` to measure what they are worth; the number will look far
better and mean far less.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from bodhi.boi import features as bx  # noqa: E402
from bodhi.boi.dataset import load_alerts, leakage_present  # noqa: E402
from bodhi.boi.model import BOIConfig, BOIModel  # noqa: E402
from bodhi.boi.schema import LEAKAGE_COLUMNS, load_dictionary  # noqa: E402
from bodhi.config import ARTIFACTS, ensure_dirs  # noqa: E402

OUT_DIR = ARTIFACTS / "boi"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", required=True, type=Path,
                    help="the organisers' training extract")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--allow-leakage", action="store_true",
                    help="let the model use the resolution-status columns")
    ap.add_argument("--no-engineered", action="store_true",
                    help="skip the engineered cross-column features")
    ap.add_argument("--holdout", type=float, default=0.2,
                    help="fraction held out for a final untouched check")
    ap.add_argument("--topk", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    ensure_dirs()
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Loading {args.train} ...")
    X, y, report = load_alerts(args.train, allow_leakage=args.allow_leakage)
    print("\nAlignment report")
    print(report.render())

    if y is None:
        print("\nERROR: the training file has no FRAUD_TGT column.", file=sys.stderr)
        return 1

    found = leakage_present(args.train)
    if found:
        state = "INCLUDED (--allow-leakage)" if args.allow_leakage else "quarantined"
        print(f"\nResolution-status columns present and {state}: {found}")
        if not args.allow_leakage:
            print("  These record how an analyst closed the alert. A model using")
            print("  them cannot score an open alert, which is the only thing")
            print("  worth scoring.")

    if not args.no_engineered:
        before = X.shape[1]
        X = bx.build(X)
        print(f"\nEngineered features: {before:,} -> {X.shape[1]:,} columns")

    # A final slice that nothing - not selection, not early stopping - touches.
    rng = np.random.default_rng(args.seed)
    idx = rng.permutation(len(X))
    n_hold = int(round(args.holdout * len(X)))
    hold_idx, fit_idx = idx[:n_hold], idx[n_hold:]

    print(f"\nTraining on {len(fit_idx):,} rows, holding out {len(hold_idx):,} ...")
    cfg = BOIConfig(topk=args.topk, random_state=args.seed)
    model = BOIModel(cfg).fit(X.iloc[fit_idx], y.iloc[fit_idx].to_numpy())

    payload = dict(model.report)
    payload["alignment"] = report.summary()
    payload["leakage_columns_in_file"] = found
    payload["leakage_allowed"] = bool(args.allow_leakage)
    payload["engineered"] = not args.no_engineered

    if n_hold > 0 and y.iloc[hold_idx].nunique() > 1:
        held = model.evaluate(X.iloc[hold_idx], y.iloc[hold_idx].to_numpy())
        payload["holdout"] = held
        print("\nHeld-out slice (never seen during selection or fitting)")
        for k, v in held.items():
            print(f"  {k:12} {v}")

    model.save(args.out)
    (args.out / "train_report.json").write_text(json.dumps(payload, indent=2,
                                                           default=str))

    imp = model.importance(25)
    if len(imp):
        desc = load_dictionary().descriptions
        print("\nTop features by gain")
        for name, share in imp.items():
            d = desc.get(name, "engineered" if name.startswith(bx.PREFIX) else "")
            print(f"  {share:6.3f}  {name:38} {d[:60]}")

    print(f"\nModel written to {args.out}")
    print(f"Predict with:  python scripts/boi_predict.py "
          f"--model {args.out} --input <validation file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
