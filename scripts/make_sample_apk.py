#!/usr/bin/env python3
"""Build the APK fixtures the dashboard's SHIELD tab and the tests use."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bodhi.config import ROOT  # noqa: E402
from bodhi.shield import triage_apk  # noqa: E402
from bodhi.shield.synth import build_benign_apk, build_sample_apk  # noqa: E402


def main() -> int:
    out = ROOT / "dashboard" / "samples"
    out.mkdir(parents=True, exist_ok=True)

    trojan = build_sample_apk(out / "bodhi_demo_trojan.apk")
    benign = build_benign_apk(out / "bodhi_demo_benign.apk")

    for path in (trojan, benign):
        report = triage_apk(path)
        print(f"{path.name:<28} {report.risk_band:<9} score={report.risk_score:5.1f}  "
              f"perms={len(report.permissions):2d}  vpa={len(report.upi_vpas)}  "
              f"packers={len(report.packers)}  obf={report.obfuscation_ratio:.2f}")
    print(f"\nWritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
