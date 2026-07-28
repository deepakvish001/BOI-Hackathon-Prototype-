#!/usr/bin/env python3
"""Build the simulated bank and write every table to ``artifacts/data``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bodhi.config import DATA, DATA_DIR, DataConfig, ensure_dirs  # noqa: E402
from bodhi.data.generator import generate, save  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--accounts", type=int, default=DATA.n_accounts)
    p.add_argument("--days", type=int, default=DATA.n_days)
    p.add_argument("--mule-rate", type=float, default=DATA.mule_rate)
    p.add_argument("--seed", type=int, default=DATA.seed)
    p.add_argument("--out", type=Path, default=DATA_DIR)
    args = p.parse_args()

    ensure_dirs()
    cfg = DataConfig(
        n_accounts=args.accounts,
        n_days=args.days,
        mule_rate=args.mule_rate,
        seed=args.seed,
        n_devices=int(args.accounts * 0.75),
        n_merchants=max(20, int(args.accounts * 0.05)),
    )
    print(f"Simulating {cfg.n_accounts:,} accounts over {cfg.n_days} days …")
    sim = generate(cfg)
    out = save(sim, args.out)

    summary = sim.summary()
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nWritten to {out}")

    print("\nRing typologies")
    print(sim.rings["typology"].value_counts().to_string())
    print("\nMule roles")
    print(sim.accounts["mule_role"].value_counts(dropna=False).to_string())
    print("\nHard negatives planted (these are the accounts that make the "
          "precision number honest)")
    for col in ("is_small_business", "legit_dormant_wake", "is_legit_collector",
                "is_bc_agent"):
        print(f"  {col:22} {int(sim.accounts[col].sum()):>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
