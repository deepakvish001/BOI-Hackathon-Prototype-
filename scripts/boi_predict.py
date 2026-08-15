#!/usr/bin/env python3
"""Score the organisers' validation extract and write a submission file.

    python scripts/boi_predict.py --model artifacts/boi --input validation.csv

Writes a CSV with the predicted probability and the binary decision. If the
input happens to carry ``FRAUD_TGT`` (for example when scoring a labelled
holdout of your own), the metrics are printed too - but the label is never fed
to the model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bodhi.boi import features as bx  # noqa: E402
from bodhi.boi.dataset import load_alerts, read_any  # noqa: E402
from bodhi.boi.model import BOIModel  # noqa: E402
from bodhi.config import ARTIFACTS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, default=ARTIFACTS / "boi")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--threshold", type=float, default=None,
                    help="override the fitted decision threshold")
    ap.add_argument("--id-column", type=str, default=None,
                    help="carry this column through to the submission file")
    ap.add_argument("--no-engineered", action="store_true")
    args = ap.parse_args()

    model = BOIModel.load(args.model)
    trained_with_engineered = any(c.startswith(bx.PREFIX) for c in model.feature_names)

    print(f"Loading {args.input} ...")
    X, y, report = load_alerts(args.input)
    print("\nAlignment report")
    print(report.render())

    if trained_with_engineered and not args.no_engineered:
        X = bx.build(X)

    missing = [c for c in model.feature_names if c not in X.columns]
    if missing:
        print(f"\n{len(missing)} model feature(s) absent from this file; "
              f"scored as missing. e.g. {missing[:5]}")

    proba = model.predict_proba(X)
    threshold = args.threshold if args.threshold is not None else model.threshold
    pred = (proba >= threshold).astype(int)

    out = pd.DataFrame({"FRAUD_TGT_PROBA": proba, "FRAUD_TGT_PRED": pred})
    if args.id_column:
        raw = read_any(args.input)
        if args.id_column in raw.columns:
            out.insert(0, args.id_column, raw[args.id_column].to_numpy())
        else:
            print(f"\nWarning: id column {args.id_column!r} not in the input.")

    dest = args.out or (args.input.parent / f"{args.input.stem}_predictions.csv")
    out.to_csv(dest, index=False)

    print(f"\nScored {len(out):,} rows at threshold {threshold:.4f}")
    print(f"  flagged      {int(pred.sum()):,} ({pred.mean():.2%})")
    print(f"  mean p       {proba.mean():.4f}")
    print(f"  written to   {dest}")

    if y is not None and y.nunique() > 1:
        metrics = model.evaluate(X, y.to_numpy())
        print("\nThis file carried labels, so for reference:")
        for k, v in metrics.items():
            print(f"  {k:12} {v}")
        (dest.parent / f"{dest.stem}_metrics.json").write_text(
            json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
