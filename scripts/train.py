#!/usr/bin/env python3
"""Train every layer and persist the models to ``artifacts/models``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from bodhi.config import DATA_DIR, MODEL_DIR, ensure_dirs  # noqa: E402
from bodhi.data.generator import load  # noqa: E402
from bodhi.engine.pipeline import MuleHunterEngine  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=DATA_DIR)
    p.add_argument("--out", type=Path, default=MODEL_DIR)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    ensure_dirs()
    if not (args.data / "accounts.parquet").exists() and \
       not (args.data / "accounts.csv").exists():
        print(f"No data in {args.data}. Run `make data` first.", file=sys.stderr)
        return 1

    sim = load(args.data)
    print(f"Loaded {len(sim.accounts):,} accounts, {len(sim.transactions):,} "
          f"transactions, {int(sim.accounts['is_mule'].sum())} mules "
          f"({sim.accounts['is_mule'].mean():.2%})")

    engine = MuleHunterEngine().attach(
        sim.accounts, sim.transactions, sim.fraud_alerts, sim.iocs, sim.regulatory)

    t0 = time.perf_counter()
    report = engine.train(verbose=not args.quiet)
    engine.score_all()
    elapsed = time.perf_counter() - t0

    out = engine.save(args.out)
    report["fusion_weights"] = engine.fusion.layer_weights
    report["total_seconds"] = round(elapsed, 2)

    print("\n" + json.dumps(report, indent=2, default=str))

    imp = engine.xgb.global_importance().head(15)
    print("\nTop features by total gain")
    for name, value in imp.items():
        bar = "#" * int(value * 120)
        print(f"  {name:28} {value:6.3f} {bar}")

    scores = engine.last_result.scores
    y = sim.accounts.set_index("account_id")["is_mule"].reindex(scores.index).to_numpy()
    for band in ("CRITICAL", "HIGH", "MEDIUM", "SAFE"):
        mask = (scores["band"] == band).to_numpy()
        if mask.sum():
            print(f"  {band:9} n={int(mask.sum()):>6}  "
                  f"mule rate {np.nanmean(y[mask].astype(float)):.3%}")

    print(f"\nModels written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
