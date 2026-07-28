#!/usr/bin/env python3
"""Capture the live investigator console for the report and the deck.

Screenshots are taken from the running application, not mocked up. If the
dashboard is broken, the deck will show it - which is the point.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from bodhi.config import ROOT  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8090")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    shots: list[str] = []

    with sync_playwright() as pw:
        launch: dict = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if Path(CHROME).exists():
            launch["executable_path"] = CHROME
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": args.width, "height": args.height},
                                device_scale_factor=2)

        def shot(name: str, full: bool = False) -> None:
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path), full_page=full)
            shots.append(name)
            print(f"  {name}.png")

        print("Capturing …")
        page.goto(args.base, wait_until="networkidle", timeout=180_000)
        page.wait_for_selector(".alert-row", timeout=180_000)
        page.wait_for_timeout(2_500)
        shot("01_investigate_queue")

        # Attribution (SHAP) panel
        page.click('#detail .subtabs button[data-panel="shap"]')
        page.wait_for_timeout(1_200)
        shot("02_shap_attribution")

        # Network panel - wait for the force layout and the explainer
        page.click('#detail .subtabs button[data-panel="graph"]')
        page.wait_for_selector("#graph-svg circle", timeout=120_000)
        page.wait_for_timeout(9_000)          # GNNExplainer runs on demand
        shot("03_network_graph")

        # Money trail
        page.click('#detail .subtabs button[data-panel="flows"]')
        page.wait_for_timeout(4_000)
        shot("04_money_trail")

        # Actions / kill-switch
        page.click('#detail .subtabs button[data-panel="actions"]')
        page.wait_for_timeout(800)
        page.click('#panel-actions [data-act="FULL_FREEZE"]')
        page.wait_for_timeout(3_000)
        shot("05_killswitch")

        # Rings
        page.click('nav.tabs button[data-view="rings"]')
        page.wait_for_timeout(3_000)
        shot("06_fraud_rings")

        # Live scoring
        page.click('nav.tabs button[data-view="live"]')
        page.wait_for_timeout(600)
        page.click("#btn-live-mule")
        page.wait_for_timeout(2_500)
        page.click("#btn-live-random")
        page.wait_for_timeout(2_500)
        shot("07_live_scoring")

        # SHIELD
        page.click('nav.tabs button[data-view="shield"]')
        page.wait_for_timeout(600)
        page.click("#btn-shield-sample")
        page.wait_for_timeout(9_000)
        shot("08_shield_apk")

        # Pipeline
        page.click('nav.tabs button[data-view="pipeline"]')
        page.wait_for_timeout(2_500)
        shot("09_agent_pipeline", full=True)

        # Compliance
        page.click('nav.tabs button[data-view="compliance"]')
        page.wait_for_timeout(2_500)
        shot("10_compliance_audit")

        browser.close()

    print(f"\n{len(shots)} screenshots written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
