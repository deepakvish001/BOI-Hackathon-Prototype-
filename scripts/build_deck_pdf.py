#!/usr/bin/env python3
"""Render the submission deck to PDF, for reviewers without PowerPoint.

``build_deck.py`` produces the editable .pptx and is the authoritative deck.
This script produces a matching 16:9 PDF. LibreOffice would normally do the
conversion, but only ``libreoffice-core`` is present in many containers (no
Impress filters), so the slides are laid out as print-styled HTML and rendered
by headless Chromium instead.

Both builders read their numbers from ``artifacts/metrics/evaluation.json``, so
the deck, the report and the code cannot disagree.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_stats import code_lines_short, test_count  # noqa: E402

from bodhi.config import (  # noqa: E402
    AFFILIATION, EVENT, FIGURE_DIR, METRICS_DIR, ROOT, TEAM, TEAM_NAME,
)

OUT_HTML = ROOT / "docs" / "_deck.html"
OUT_PDF = ROOT / "docs" / "BODHI_Mule_Hunter_Deck.pdf"
SHOTS = ROOT / "docs" / "screenshots"

CHROME_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]


def _chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    raise RuntimeError("no Chromium binary found")


def _img(path: Path, **style) -> str:
    if not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode()
    css = ";".join(f"{k.replace('_', '-')}:{v}" for k, v in style.items())
    return f'<img src="data:image/png;base64,{b64}" style="{css}">'


def _pct(x, dp=1):
    return f"{100 * float(x):.{dp}f}%"


def _n(x):
    return f"{int(x):,}"


CSS = """
@page { size: 13.333in 7.5in; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  background: #0b0f17; color: #e6edf7;
}
.slide {
  width: 13.333in; height: 7.5in; padding: 0.42in 0.7in 0.5in;
  position: relative; overflow: hidden; break-after: page;
  background: #0b0f17; display: flex; flex-direction: column;
}
.slide:last-child { break-after: auto; }
.kicker { font-size: 11pt; font-weight: 700; color: #4da3ff; letter-spacing: .8px;
          text-transform: uppercase; }
h1 { font-size: 30pt; font-weight: 700; margin: 5pt 0 0; line-height: 1.14; }
.rule { width: 1.1in; height: 2.5pt; background: #4da3ff; margin: 9pt 0 0; }
.sub { font-size: 14pt; color: #8fa3c0; margin-top: 10pt; line-height: 1.35;
       max-width: 11.2in; }
.body { flex: 1; margin-top: 16pt; }
.panel { background: #121826; border: 0.75pt solid #23304a; border-radius: 9pt;
         padding: 14pt 16pt; }
.panel2 { background: #182031; border: 0.75pt solid #23304a; border-radius: 9pt;
          padding: 14pt 16pt; }
.plabel { font-size: 10pt; font-weight: 700; color: #61708a; letter-spacing: .7px;
          text-transform: uppercase; margin-bottom: 9pt; }
.row { display: flex; gap: 14pt; }
.col { flex: 1; }
.stats { display: flex; gap: 11pt; }
.stat { flex: 1; background: #182031; border: 0.75pt solid #23304a; border-radius: 9pt;
        padding: 11pt 8pt; text-align: center; }
.stat .v { font-size: 24pt; font-weight: 700; line-height: 1.1; display: block; }
.stat .l { font-size: 8.5pt; color: #61708a; text-transform: uppercase;
           letter-spacing: .5px; margin-top: 4pt; display: block; }
table { width: 100%; border-collapse: collapse; font-size: 12pt; }
th { font-size: 9.5pt; color: #61708a; text-transform: uppercase; letter-spacing: .5px;
     text-align: right; padding: 0 0 7pt; font-weight: 700;
     border-bottom: 1pt solid #23304a; }
th:first-child, td:first-child { text-align: left; }
td { padding: 7pt 0; text-align: right; color: #8fa3c0; }
td:first-child { color: #e6edf7; }
tr.hl td { color: #e6edf7; font-weight: 700; border-top: 1pt solid #4da3ff;
           border-bottom: 1pt solid #4da3ff; background: #16233c; }
.mono { font-family: Consolas, monospace; }
.dim { color: #8fa3c0; }
.faint { color: #61708a; }
.green { color: #2ecc9b; } .amber { color: #f2c14e; }
.orange { color: #ff8c42; } .red { color: #ff4d5e; }
.blue { color: #4da3ff; } .purple { color: #7d5cff; }
.foot { position: absolute; bottom: 0.3in; left: 0.7in; right: 0.7in;
        display: flex; justify-content: space-between;
        font-size: 9.5pt; color: #61708a; }
img { display: block; border: 0.75pt solid #23304a; border-radius: 6pt; }
ul { list-style: none; }
li { display: flex; gap: 9pt; margin-bottom: 9pt; font-size: 13.5pt;
     line-height: 1.3; color: #8fa3c0; }
li b { color: #e6edf7; }
li::before { content: "●"; color: #4da3ff; font-size: 8pt; margin-top: 5pt; }
.big { font-size: 19pt; font-weight: 700; line-height: 1.3; }
.callout { background: #0f2a22; border: 1pt solid #2ecc9b; border-radius: 9pt;
           padding: 14pt 18pt; font-size: 15pt; font-weight: 700; line-height: 1.3; }
.lstep { display: flex; gap: 12pt; margin-bottom: 9pt; }
.lstep .tag { font-family: Consolas, monospace; font-weight: 700; width: 0.34in; }
.lstep .nm { font-weight: 700; width: 2.5in; font-size: 12.5pt; }
.lstep .ds { color: #8fa3c0; font-size: 11.5pt; flex: 1; }
"""


def slide(inner: str, n: int | None = None, total: int = 19,
          note: str = "") -> str:
    foot = ""
    if n is not None:
        label = note or ("BODHI MULE HUNTER AI · CyberShield Hackathon 2026 · "
                         "Problem Statement 2")
        foot = (f'<div class="foot"><span>{label}</span>'
                f'<span>{n} / {total}</span></div>')
    return f'<section class="slide">{inner}{foot}</section>'


def head(kicker: str, title: str, sub: str = "") -> str:
    s = f'<div class="sub">{sub}</div>' if sub else ""
    return (f'<div class="kicker">{kicker}</div><h1>{title}</h1>'
            f'<div class="rule"></div>{s}')


def build(m: dict) -> str:
    ds, L = m["dataset"], m["layers"]
    b, c, lat = m["baseline"], m["calibration"], m["latency"]
    ind, oot = m["independence_from_tickets"], m["out_of_time"]
    thr = m["thresholds"]
    decoy = {d["population"]: d for d in m["hard_negatives"]}
    typ = {t["typology"]: t for t in m["typology"]}
    F = L["Fused (L7)"]
    S: list[str] = []
    N = 19
    authors = "".join(
        f'<div><div style="font-size:13pt;font-weight:600">{mem.name}</div>'
        f'<div class="mono faint" style="font-size:10.5pt">{mem.enrolment}</div></div>'
        for mem in TEAM)

    # 1 title
    S.append(slide(f'''
      <div style="flex:1;display:flex;flex-direction:column;justify-content:center">
        <div style="display:flex;align-items:center;gap:18pt;margin-bottom:16pt">
          <div style="width:0.85in;height:0.85in;border-radius:14pt;
               background:linear-gradient(135deg,#4da3ff,#7d5cff);display:flex;
               align-items:center;justify-content:center;font-size:32pt;
               font-weight:800;color:#06101f">B</div>
          <div>
            <div style="font-size:42pt;font-weight:800;line-height:1.05">BODHI MULE HUNTER AI</div>
            <div style="font-size:16pt;color:#4da3ff;margin-top:6pt">
              Real-time mule account &amp; suspicious transaction detection</div>
          </div>
        </div>
        <div style="font-size:15pt;color:#8fa3c0;line-height:1.4;max-width:10.5in;
             margin-bottom:26pt">
          Nine layers. Three complementary model views. One calibrated score an
          investigator can act on &mdash; and a containment layer that refuses to act
          without corroboration.</div>
        <div class="stats">
          <div class="stat"><span class="v">{F['roc_auc']:.4f}</span><span class="l">Fused ROC-AUC</span></div>
          <div class="stat"><span class="v">{F['pr_auc']:.4f}</span><span class="l">Fused PR-AUC</span></div>
          <div class="stat"><span class="v green">{_pct(b['false_positive_reduction'], 2)}</span><span class="l">False positives cut</span></div>
          <div class="stat"><span class="v green">{b['precision_uplift_x']:.0f}&times;</span><span class="l">Precision uplift</span></div>
          <div class="stat"><span class="v blue">{lat['p50_ms']:.3f} ms</span><span class="l">Inline decision p50</span></div>
        </div>
        <div style="display:flex;gap:26pt;margin-top:24pt">{authors}</div>
        <div style="font-size:11.5pt;color:#61708a;margin-top:16pt">
          {TEAM_NAME} &nbsp;·&nbsp; {EVENT} &nbsp;·&nbsp; {AFFILIATION}</div>
      </div>'''))

    # 2 problem
    S.append(slide(head("The problem", "A mule account is a normal account",
        "Real KYC. Real customer. Payments clear. Nothing about the account is "
        "anomalous &mdash; only the shape of the money moving through it, and the "
        "company it keeps.") + f'''
      <div class="body row">
        <div class="col panel">
          <div class="plabel">How a mule ring actually works</div>
          <div class="lstep"><div class="nm blue" style="width:1.5in">1 &nbsp;Victims</div>
            <div class="ds">40 defrauded people push money into one collector, in 90 minutes</div></div>
          <div class="lstep"><div class="nm blue" style="width:1.5in">2 &nbsp;Layering</div>
            <div class="ds">A &rarr; B &rarr; C &rarr; D within minutes, each hop keeping 2&ndash;10%</div></div>
          <div class="lstep"><div class="nm blue" style="width:1.5in">3 &nbsp;Structuring</div>
            <div class="ds">Split into transfers parked just under the reporting limit</div></div>
          <div class="lstep"><div class="nm blue" style="width:1.5in">4 &nbsp;Cash-out</div>
            <div class="ds">Drained via ATM/AePS at 03:12 &mdash; out of the banking system</div></div>
        </div>
        <div class="col panel">
          <div class="plabel">Why today's rule engines fail</div>
          <div style="font-size:13pt;color:#8fa3c0;line-height:1.4;margin-bottom:16pt">
            They score accounts in isolation, so they cannot express &ldquo;this
            account's counterparties are themselves suspicious&rdquo; or &ldquo;these
            forty credits arrived in ninety minutes&rdquo;. Lacking the vocabulary,
            they compensate with sensitivity.</div>
          <div class="stats">
            <div class="stat"><span class="v orange">{_n(b['alerts'])}</span><span class="l">alerts raised</span></div>
            <div class="stat"><span class="v red">{_pct(b['precision'], 2)}</span><span class="l">precision</span></div>
          </div>
        </div>
      </div>
      <div class="big" style="margin-top:6pt">99 of every 100 investigations are
        wasted. That is the number we set out to fix.</div>''', 2, N))

    # 3 insight
    S.append(slide(head("The core insight",
        "Aggregates destroy the signal that matters",
        "The same monthly totals describe a shopkeeper and a mule. Only the ordering "
        "separates them &mdash; and no aggregate preserves order.") + '''
      <div class="body row">
        <div class="col panel2">
          <div class="plabel green">Shopkeeper</div>
          <div style="font-size:15pt;line-height:1.4;margin-bottom:14pt">
            &ldquo;Received &#8377;4 lakh from 38 payers over the month.&rdquo;</div>
          <div class="dim" style="font-size:13pt;line-height:1.4">
            Spread across weeks. Money rests. Counterparties repeat.</div>
        </div>
        <div class="col panel2">
          <div class="plabel red">Mule</div>
          <div style="font-size:15pt;line-height:1.4">
            &ldquo;Received &#8377;4 lakh from 38 payers between 14:10 and 15:40,
            then withdrew &#8377;3.8 lakh in nineteen ATM visits beginning at
            03:12.&rdquo;</div>
        </div>
      </div>
      <div class="big">Identical aggregates. Completely different sequence.</div>
      <div class="dim" style="font-size:13.5pt;line-height:1.4;margin-top:10pt;max-width:11.5in">
        So the ensemble is not three flavours of the same tabular model. It is a
        tabular model, a graph neural network and a temporal model &mdash; each seeing
        something the other two structurally cannot.</div>''', 3, N))

    # 4 architecture
    def layer_row(tag, col, name, desc):
        return (f'<div class="lstep"><div class="tag {col}">{tag}</div>'
                f'<div class="nm">{name}</div><div class="ds">{desc}</div></div>')

    S.append(slide(head("Architecture", "Nine layers, each owned by a named agent",
        "Declared input/output contracts, so the provenance of every decision is "
        "recoverable from the audit trail.") + f'''
      <div class="body">
        <div class="panel" style="margin-bottom:10pt">
          <div class="plabel blue">Ingest &amp; enrich</div>
          {layer_row("L1", "blue", "Transaction ingestion", "UPI · IMPS · NEFT · RTGS · AePS · ATM · card + NCRP tickets + RBI feeds")}
          {layer_row("L2", "blue", "Feature engineering", "60 features: velocity, fan-in/out, dormancy, burst, structuring, device reuse")}
          {layer_row("L3", "blue", "Graph construction", "accounts × devices × IPs × VPAs, shared-identifier cliques capped")}
        </div>
        <div class="panel" style="margin-bottom:10pt">
          <div class="plabel purple">Detect</div>
          {layer_row("L4", "purple", "XGBoost screening", "tabular behaviour → exact TreeSHAP attributions")}
          {layer_row("L5", "purple", "GraphSAGE", "network structure → rings, device farms, multi-hop layering")}
          {layer_row("L6", "purple", "Temporal graph network", "event ordering → smurfing, dormant bursts, rapid routing")}
        </div>
        <div class="panel">
          <div class="plabel green">Decide &amp; act</div>
          {layer_row("L7", "green", "Risk fusion", "monotone non-negative blend, isotonic calibrated 0–100")}
          {layer_row("L8", "green", "Explainability", "TreeSHAP + GNNExplainer + plain-English narrative")}
          {layer_row("L9", "green", "Alerting &amp; kill-switch", "proportionate, corroborated, reversible, audited")}
        </div>
      </div>
      <div class="faint" style="font-size:11.5pt;line-height:1.35">
        BODHI SHIELD (APK static triage) feeds extracted UPI handles and beneficiary
        accounts into Layer 3 &mdash; priming the graph before the first victim
        installs the trojan.</div>''', 4, N))

    # 5 three views
    def view_card(tag, name, col, sees, catches, blind, auc):
        return f'''<div class="col panel">
          <div class="plabel {col}">{tag}</div>
          <div style="font-size:21pt;font-weight:700;margin-bottom:16pt">{name}</div>
          <div class="plabel" style="margin-bottom:4pt">Sees</div>
          <div class="dim" style="font-size:12.5pt;margin-bottom:12pt">{sees}</div>
          <div class="plabel" style="margin-bottom:4pt">Catches</div>
          <div class="dim" style="font-size:12.5pt;margin-bottom:12pt">{catches}</div>
          <div class="plabel" style="margin-bottom:4pt">Blind to</div>
          <div class="orange" style="font-size:12.5pt;margin-bottom:14pt">{blind}</div>
          <div class="mono {col}" style="font-size:13pt;font-weight:700">{auc}</div>
        </div>'''

    S.append(slide(head("Layers 4–6",
        "Three views that cannot see each other's evidence") + f'''
      <div class="body row">
        {view_card("Layer 4", "XGBoost", "blue", "Aggregates per account",
                   "Volume, ratios, cash-out share, structuring",
                   "Who the counterparties are", f"AUC {L['XGBoost (L4)']['roc_auc']:.4f}")}
        {view_card("Layer 5", "GraphSAGE", "purple", "Two-hop neighbourhood",
                   "Rings, device farms, multi-hop layering",
                   "When things happened", f"AUC {L['GraphSAGE (L5)']['roc_auc']:.4f}")}
        {view_card("Layer 6", "Temporal GNN", "green", "Last 64 events, in order",
                   "Smurfing, dormant bursts, rapid routing",
                   "Anything outside the account", f"AUC {L['TGN (L6)']['roc_auc']:.4f}")}
      </div>
      <div class="panel2" style="font-size:12.5pt;line-height:1.35;color:#8fa3c0">
        Both neural layers are implemented in <b style="color:#e6edf7">NumPy with
        hand-derived gradients</b> &mdash; no PyTorch, no CUDA, no 2 GB of wheels.
        Every analytic gradient is verified against central finite differences in the
        test suite, so &ldquo;hand-rolled&rdquo; does not mean
        &ldquo;unverified&rdquo;.</div>''', 5, N))

    # 6 fusion
    S.append(slide(head("Layer 7",
        "Fusion: two properties that are not negotiable") + f'''
      <div class="body">
        <div class="row" style="margin-bottom:14pt">
          <div class="col panel">
            <div class="plabel blue">Monotonicity</div>
            <div class="dim" style="font-size:13pt;line-height:1.4">
              Blend weights are constrained non-negative, so more evidence can never
              lower a score. An unconstrained stacker on a small fold reliably learns
              &ldquo;higher temporal score means safer&rdquo; &mdash; indefensible to
              an investigator, unshippable past a regulator.</div>
          </div>
          <div class="col panel">
            <div class="plabel green">Calibration</div>
            <div class="dim" style="font-size:13pt;line-height:1.4;margin-bottom:12pt">
              The score drives an automated kill-switch at 85, so it has to mean what
              it says. Isotonic regression maps the blend onto observed frequencies.</div>
            <div class="stats">
              <div class="stat"><span class="v green" style="font-size:19pt">{c['brier']:.5f}</span><span class="l">Brier score</span></div>
              <div class="stat"><span class="v green" style="font-size:19pt">{c['ece']:.4f}</span><span class="l">Expected calib. error</span></div>
            </div>
          </div>
        </div>
        <div class="panel2">
          <div class="plabel">Four folds, not three</div>
          <div class="row">
            <div class="col"><div class="mono blue" style="font-size:15pt;font-weight:700">train</div>
              <div class="dim" style="font-size:11.5pt">fits the base models</div></div>
            <div class="col"><div class="mono purple" style="font-size:15pt;font-weight:700">val</div>
              <div class="dim" style="font-size:11.5pt">early-stops them</div></div>
            <div class="col"><div class="mono green" style="font-size:15pt;font-weight:700">fuse</div>
              <div class="dim" style="font-size:11.5pt">fits the Layer-7 blend</div></div>
            <div class="col"><div class="mono orange" style="font-size:15pt;font-weight:700">test</div>
              <div class="dim" style="font-size:11.5pt">touched exactly once</div></div>
          </div>
        </div>
      </div>
      <div class="faint" style="font-size:11.5pt">A stacker fitted on the fold that
        early-stopped its own inputs inherits their optimism &mdash; and came out
        worse than the best single layer.</div>''', 6, N))

    # 7 results
    def _layer_row(name: str, v: dict) -> str:
        cls = ' class="hl"' if name.startswith("Fused") else ""
        return (f"<tr{cls}><td>{name}</td><td>{v['roc_auc']:.4f}</td>"
                f"<td>{v['pr_auc']:.4f}</td><td>{v['precision_at_1pct']:.3f}</td>"
                f"<td>{v['recall_at_1pct']:.3f}</td></tr>")

    rows = "".join(_layer_row(name, v) for name, v in L.items())
    S.append(slide(head("Results", "Every layer, on held-out data",
        f"{_n(ds['accounts'])} accounts · {_n(ds['transactions'])} transactions · "
        f"{_n(ds['mule_accounts'])} mules ({_pct(ds['mule_rate'], 2)}) · {ds['rings']} rings")
        + f'''
      <div class="body row">
        <div class="col" style="flex:1.15">
          <table><thead><tr><th>Layer</th><th>ROC-AUC</th><th>PR-AUC</th>
            <th>P@1%</th><th>R@1%</th></tr></thead><tbody>{rows}</tbody></table>
          <div class="dim" style="font-size:12.5pt;line-height:1.35;margin-top:16pt">
            The fused model beats every individual view on both ranking metrics
            &mdash; which is the complementarity claim, measured.</div>
          <div class="stats" style="margin-top:16pt">
            <div class="stat"><span class="v green" style="font-size:20pt">{_pct(oot['recall'], 0)}</span><span class="l">out-of-time recall</span></div>
            <div class="stat"><span class="v blue" style="font-size:20pt">{_pct(ind['share_of_detections_not_in_any_ticket'], 0)}</span><span class="l">never in any NCRP ticket</span></div>
            <div class="stat"><span class="v purple" style="font-size:20pt">{lat['p99_ms']:.3f} ms</span><span class="l">inline decision p99</span></div>
          </div>
        </div>
        <div class="col">{_img(FIGURE_DIR / 'roc_pr.png', width="100%", background="#fff", padding="6pt")}</div>
      </div>''', 7, N))

    # 8 vs rules
    def cmp_rows(pairs, cls=""):
        return "".join(
            f'<div style="display:flex;justify-content:space-between;margin-bottom:14pt">'
            f'<span class="dim" style="font-size:14pt">{k}</span>'
            f'<span class="{cls}" style="font-size:17pt;font-weight:700">{v}</span></div>'
            for k, v in pairs)

    S.append(slide(head("The headline", "Versus the rule engine it replaces",
        "Compared at identical recall &mdash; because a system that alerts on "
        "everything can always claim to catch more.") + f'''
      <div class="body row" style="align-items:stretch">
        <div class="col panel">
          <div class="plabel orange">Rule engine · 8 scenarios</div>
          {cmp_rows([("Alerts raised", _n(b["alerts"])),
                     ("True positives", _n(b["true_positives"])),
                     ("False positives", _n(b["false_positives"])),
                     ("Precision", _pct(b["precision"], 2))], "red")}
        </div>
        <div style="display:flex;align-items:center;font-size:26pt;color:#4da3ff">&rarr;</div>
        <div class="col panel2" style="border-color:#2ecc9b;border-width:1.5pt">
          <div class="plabel green">BODHI · same recall</div>
          {cmp_rows([("Alerts raised", _n(b["bodhi_alerts_at_equal_recall"])),
                     ("True positives", _n(b["bodhi_true_positives_at_equal_recall"])),
                     ("False positives", _n(b["bodhi_false_positives_at_equal_recall"])),
                     ("Precision", _pct(b["bodhi_precision_at_equal_recall"], 2))], "green")}
        </div>
      </div>
      <div class="callout">{_pct(b['false_positive_reduction'], 2)} of false positives
        eliminated at unchanged recall &mdash; a {b['precision_uplift_x']:.0f}&times;
        precision uplift. The analyst clears the same true positives while opening
        {_n(b['bodhi_alerts_at_equal_recall'])} cases instead of
        {_n(b['alerts'])}.</div>''', 8, N))

    # 9 credibility
    drows = "".join(
        f'<tr><td>{label}</td><td>{_n(decoy[k]["n"])}</td>'
        f'<td class="{"green" if decoy[k]["false_positive_rate"] < 0.01 else "amber"}" '
        f'style="font-weight:700">{_pct(decoy[k]["false_positive_rate"], 2)}</td></tr>'
        for k, label in [
            ("bc_agent_device_cluster", "Business Correspondent (Bank Mitra) agents"),
            ("community_collector", "Community collectors (chit fund, tuition)"),
            ("small_business_fan_in", "Small businesses with heavy fan-in"),
            ("legitimate_dormant_wake", "Genuine dormancy reactivation"),
            ("ordinary_retail", "Ordinary retail")] if k in decoy)
    S.append(slide(head("Why that number is credible",
        "We planted the accounts designed to break it",
        "Any model scores ~1.0 AUC on naive synthetic fraud data. So the simulated "
        "bank is stocked with legitimate accounts that are structurally "
        "indistinguishable from mules.") + f'''
      <div class="body row">
        <div class="col" style="flex:1.35">
          <table><thead><tr><th>Legitimate population</th><th>Count</th>
            <th>False-positive rate</th></tr></thead><tbody>{drows}</tbody></table>
        </div>
        <div class="col panel2">
          <div class="plabel orange">The hardest decoy</div>
          <div class="dim" style="font-size:13pt;line-height:1.4">
            A Business Correspondent legitimately operates 8&ndash;22 village accounts
            from one handheld and dispenses AePS cash all day.<br><br>
            That is the exact fingerprint of a device farm doing rapid cash-out
            &mdash; and the system does not fall for it.</div>
        </div>
      </div>
      <div class="plabel">Other guards against flattering ourselves</div>
      <div class="row">
        <div class="col"><b style="font-size:12.5pt">Four disjoint folds</b>
          <div class="dim" style="font-size:11pt">test touched exactly once</div></div>
        <div class="col"><b style="font-size:12.5pt">Cross-fitted seed model</b>
          <div class="dim" style="font-size:11pt">no label leaks into a neighbour's average</div></div>
        <div class="col"><b style="font-size:12.5pt">Intelligence quarantined</b>
          <div class="dim" style="font-size:11pt">NCRP tickets never reach the supervised layers</div></div>
        <div class="col"><b style="font-size:12.5pt">Point-in-time correctness</b>
          <div class="dim" style="font-size:11pt">tested against a truncated ledger</div></div>
      </div>''', 9, N))

    # 10 weakness
    trows = "".join(
        f'<tr><td>{k.replace("role:", "role: ").replace("_", " ")}</td>'
        f'<td class="{"red" if t["recall"] < 0.90 else ("amber" if t["recall"] < 0.99 else "green")}" '
        f'style="font-weight:700">{t["recall"]:.1%}</td>'
        f'<td>{t["detected"]}/{t["mules"]}</td></tr>'
        for k, t in sorted(typ.items(), key=lambda kv: kv[1]["recall"])[:9])
    S.append(slide(head("Where it is weak",
        "Recall is not uniform, and averaging hides that",
        "A deployment team needs to know which typologies still need human-designed "
        "scenarios alongside the model.") + f'''
      <div class="body row">
        <div class="col">
          <table><thead><tr><th>Typology / role</th><th>Recall</th>
            <th>Detected</th></tr></thead><tbody>{trows}</tbody></table>
        </div>
        <div class="col panel2">
          <div class="plabel orange">Why structuring is the hardest</div>
          <div class="dim" style="font-size:12.5pt;line-height:1.4;margin-bottom:16pt">
            A single sub-threshold transfer is, by construction, indistinguishable
            from a genuine near-threshold property or jewellery payment. Detection
            depends on an aggregate pattern that only becomes visible once enough
            transfers have accumulated.</div>
          <div class="plabel orange">And cash-out accounts</div>
          <div class="dim" style="font-size:12.5pt;line-height:1.4">
            Short, thin histories &mdash; they receive once and drain. There is
            simply less evidence to work with.</div>
        </div>
      </div>
      <div class="big">We report these rather than averaging them away. A prototype
        that hides its blind spots is not useful to the team that has to deploy
        it.</div>''', 10, N))

    # 11 console
    S.append(slide(head("Live demo", "The investigator console",
        "Four layer scores shown separately, evidence written in the vocabulary of an "
        "AML desk, cluster and exposure on every card.")
        + f'<div class="body">{_img(SHOTS / "01_investigate_queue.png", width="100%")}</div>',
        11, N))

    # 12 explainability
    S.append(slide(head("Layer 8", "Explanations specific enough to file") + f'''
      <div class="body row">
        <div class="col">{_img(SHOTS / "02_shap_attribution.png", width="100%")}
          <div class="dim" style="font-size:12pt;line-height:1.35;margin-top:12pt">
            Exact TreeSHAP &mdash; attributions sum to the model's margin, not an
            approximation of it. Rendered as sentences, not feature names.</div></div>
        <div class="col">{_img(SHOTS / "03_network_graph.png", width="100%")}
          <div class="dim" style="font-size:12pt;line-height:1.35;margin-top:12pt">
            GNNExplainer answers which <i>relationships</i> drove the score. Dashed
            edges are shared-device links, invisible to any tabular model.</div></div>
      </div>''', 12, N))

    # 13 money trail
    S.append(slide(head("Following the money", "Time-respecting multi-hop trails",
        "Every hop must occur after the previous one &mdash; money cannot be forwarded "
        "before it arrives. Value retention exposes each mule's commission.") + f'''
      <div class="body row">
        <div class="col" style="flex:1.9">{_img(SHOTS / "04_money_trail.png", width="100%")}</div>
        <div class="col panel2">
          <div class="plabel blue">The tell</div>
          <div class="dim" style="font-size:13pt;line-height:1.4">
            A laundering chain leaks 2&ndash;10% at every hop as the handler's cut.
            <br><br>
            Legitimate payment chains simply do not have that shape &mdash; which is
            why value retention is shown on every path.</div>
        </div>
      </div>''', 13, N))

    # 14 containment
    cons = "".join(
        f'<div class="panel2" style="padding:9pt 14pt;margin-bottom:8pt;display:flex;gap:14pt">'
        f'<b class="blue" style="width:2.1in;font-size:13pt">{k}</b>'
        f'<span class="dim" style="font-size:12pt">{v}</span></div>'
        for k, v in [
            ("Proportionality", "below 85 the automation cannot freeze at all"),
            ("Corroboration", "a full freeze needs ≥2 independent layers agreeing"),
            ("Reversibility", "every action carries a TTL and can be reverted"),
            ("Rate limiting", "bounded automated freezes per hour caps blast radius"),
            ("Protected accounts", "salary, pension and benefit accounts go to a human")])
    S.append(slide(head("Layer 9", "Containment that can refuse",
        "A wrongly frozen account is somebody unable to pay for medicine. No AUC "
        "improvement justifies being casual about that.") + f'''
      <div class="body row">
        <div class="col">{cons}</div>
        <div class="col">{_img(SHOTS / "05_killswitch.png", width="100%")}</div>
      </div>
      <div class="dim" style="font-size:12.5pt;line-height:1.35">
        When a constraint fires, the action is <b style="color:#e6edf7">downgraded</b>
        &mdash; never escalated &mdash; and flagged for human review. Every decision,
        including every refusal, is written to a hash-chained audit log whose head hash
        can be externally anchored.</div>''', 14, N))

    # 15 shield
    steps = "".join(
        f'<div style="display:flex;gap:12pt;margin-bottom:14pt">'
        f'<span class="mono purple" style="font-weight:700;font-size:15pt">{i}</span>'
        f'<div><b style="font-size:13pt">{k}</b>'
        f'<div class="dim" style="font-size:11.5pt;line-height:1.3">{v}</div></div></div>'
        for i, (k, v) in enumerate([
            ("Parse manifest", "binary AndroidManifest.xml string pool, to spec"),
            ("Score combinations", "READ_SMS alone is a messaging app; + accessibility + overlay is a trojan"),
            ("Mine the DEX", "UPI handles, IFSC codes, accounts, C2 endpoints"),
            ("Hand off", "identifiers become weighted nodes in the fraud graph")], 1))
    S.append(slide(head("Ecosystem synergy", "BODHI SHIELD → BODHI MULE HUNTER",
        "Malware analysis priming fraud controls before the first victim installs "
        "the app.") + f'''
      <div class="body row">
        <div class="col" style="flex:1.9">{_img(SHOTS / "08_shield_apk.png", width="100%")}</div>
        <div class="col">{steps}</div>
      </div>''', 15, N))

    # 16 deployment
    boxes = "".join(
        f'<div class="col panel2" style="border-color:{col};text-align:center">'
        f'<div style="font-size:13pt;font-weight:700;line-height:1.25">{t}</div>'
        f'<div class="dim" style="font-size:10.5pt;margin-top:8pt">{s}</div></div>'
        + ('<div style="display:flex;align-items:center;color:#4da3ff;font-size:18pt">&rarr;</div>'
           if i < 3 else '')
        for i, (t, s, col) in enumerate([
            ("Core banking / switch", "Kafka event stream", "#4da3ff"),
            ("L1–L2 streaming features", "feature store", "#4da3ff"),
            ("L3/L5/L6 batch<br>(scheduled)", "standing risk cache", "#7d5cff"),
            ("Inline decision<br>&lt; 0.1 ms", "ALLOW · REVIEW · HOLD · BLOCK", "#2ecc9b")]))
    perf = "".join(
        f'<div style="display:flex;justify-content:space-between;margin-bottom:7pt">'
        f'<span class="dim" style="font-size:12.5pt">{k}</span>'
        f'<span class="mono" style="font-size:12.5pt;font-weight:700">{v}</span></div>'
        for k, v in [("Simulate the bank", "7 s"), ("Feature engineering", "~4 s"),
                     ("Graph construction", "~3 s"), ("Full training, all layers", "~123 s"),
                     ("Full population re-score", "~38 s"),
                     ("Inline decision (p50)", f"{lat['p50_ms']:.3f} ms")])
    S.append(slide(head("Deployment", "How it fits a real bank",
        "The split between scheduled graph work and the inline path is what makes a "
        "UPI-latency decision possible at all.") + f'''
      <div class="body">
        <div class="row" style="margin-bottom:20pt">{boxes}</div>
        <div class="row">
          <div class="col"><div class="plabel">Measured performance</div>{perf}</div>
          <div class="col panel2">
            <div class="plabel">Why this split</div>
            <div class="dim" style="font-size:12.5pt;line-height:1.4">
              The graph and temporal layers run on a schedule and cache a standing
              risk per account. The authorisation path combines those cached scores
              with the transaction's own attributes and never traverses the graph
              &mdash; which is the only way a decision is feasible at payment
              latencies.</div>
          </div>
        </div>
      </div>''', 16, N))

    # 17 their dataset
    boi_path = METRICS_DIR / "boi_track.json"
    if boi_path.exists():
        bm = json.loads(boi_path.read_text())
        bd, bdep, bleak = bm["dataset"], bm["deployable"], bm["leakage_effect"]
        bcv, bhold = bdep["cv"], bdep["holdout"]
        bsel = bcv[bdep["selected_strategy"]]
        brows = "".join(
            f'<tr><td>{label}</td><td>{_n(bcv[k]["n_features"])}</td>'
            f'<td>{bcv[k]["roc_auc"]:.3f}</td><td>{bcv[k]["pr_auc"]:.3f}</td></tr>'
            for k, label in [("bank_finalized", "Bank's 18 finalised"),
                             ("bank_plus_engineered", "Bank + engineered"),
                             ("auto_topk", "Automatic top-<i>k</i>"),
                             ("all", "Every column")] if k in bcv)
        S.append(slide(head("Their dataset",
            "Built on the schema they published",
            f"{_n(bd['declared_columns'])} declared columns, one row per alert. The "
            f"names are machine-generated from a grammar, so we parse them instead of "
            f"treating them as opaque.") + f'''
      <div class="body row" style="align-items:stretch">
        <div class="col panel2" style="border-color:#ff4d5e;border-width:1.5pt">
          <div class="plabel red">Four columns leak the label</div>
          <div class="dim" style="font-size:12.5pt;line-height:1.4;margin-bottom:16pt">
            <b style="color:#e6edf7">FRAUD_SUSPECTED · FALSE_POSITIVE ·
            OTHER_RESOLUTION · UNATTENDED</b> are resolution-status flags &mdash;
            how an analyst <i>closed</i> the alert. An open alert has none of them.</div>
          <div style="display:flex;justify-content:space-between;margin-bottom:10pt">
            <span class="dim" style="font-size:12.5pt">PR-AUC, quarantined (deployable)</span>
            <span class="green mono" style="font-size:16pt;font-weight:700">{bleak['pr_auc_deployable']:.3f}</span></div>
          <div style="display:flex;justify-content:space-between">
            <span class="dim" style="font-size:12.5pt">PR-AUC, resolution columns admitted</span>
            <span class="red mono" style="font-size:16pt;font-weight:700">{bleak['pr_auc_with_leakage']:.3f}</span></div>
        </div>
        <div class="col">
          <div class="plabel">Four strategies, selection inside every fold</div>
          <table><thead><tr><th>Strategy</th><th>Feats</th><th>ROC-AUC</th>
            <th>PR-AUC</th></tr></thead><tbody>{brows}</tbody></table>
          <div class="dim" style="font-size:12pt;line-height:1.35;margin-top:14pt">
            The bank's eighteen expert-chosen columns beat all
            {_n(bcv['all']['n_features'])}. With {_n(bd['positives'])} positives
            against thousands of predictors, that is what theory predicts.</div>
        </div>
      </div>
      <div class="stats" style="margin-bottom:14pt">
        <div class="stat"><span class="v green" style="font-size:20pt">{bhold['roc_auc']:.4f}</span><span class="l">untouched holdout ROC-AUC</span></div>
        <div class="stat"><span class="v blue" style="font-size:20pt">{bsel['roc_auc']:.4f}</span><span class="l">cross-validated estimate</span></div>
        <div class="stat"><span class="v red" style="font-size:20pt">{bleak['multiple']:.1f}&times;</span><span class="l">PR-AUC inflation if leaked</span></div>
        <div class="stat"><span class="v purple" style="font-size:20pt">{_n(bd['declared_columns'])}</span><span class="l">columns parsed by grammar</span></div>
      </div>
      <div class="callout" style="background:#2a1a0f;border-color:#ff8c42;
           font-size:12.5pt;padding:11pt 16pt">
        Measured on a stand-in table with their exact schema &mdash; their data was not
        released when this was built. It proves the pipeline runs and does not flatter
        itself. It is not model performance, and we will not present it as such.</div>''', 17, N))

    # 18 limitations
    lims = "".join(
        f'<div style="display:flex;gap:18pt;margin-bottom:13pt">'
        f'<b class="orange" style="width:3.1in;font-size:13pt">{k}</b>'
        f'<span class="dim" style="font-size:12.5pt;line-height:1.35;flex:1">{v}</span></div>'
        for k, v in [
            ("Simulated data", "Demonstrates the architecture beats a rule engine on data with realistic decoys. Not a forecast of production performance."),
            ("Static APK analysis only", "The dynamic sandbox with runtime hooking needs an instrumented Android image; it cannot ship self-contained."),
            ("Graph layers are not real-time", "~38 s full re-score. An account whose neighbourhood changed since the last batch is scored on slightly stale structure."),
            ("Single-institution view", "Rings routing through several banks are only partly visible — a data-sharing problem, not a modelling one."),
            ("Cold start", "The strongest feature is neighbourhood risk propagated from confirmed cases; with zero known mules performance drops materially."),
            ("Disparate impact unevaluated", "Minimum-KYC and shared-device features are predictive but correlate with lower-income households. Alert-rate parity must be measured before go-live.")])
    S.append(slide(head("Limitations",
        "Stated plainly, because hiding them helps nobody")
        + f'<div class="body">{lims}</div>', 18, N))

    # 19 close
    S.append(slide(f'''
      <div style="flex:1;display:flex;flex-direction:column;justify-content:center">
        <div style="font-size:32pt;font-weight:700;line-height:1.22;margin-bottom:16pt">
          Mule detection fails today not because the<br>signal is absent</div>
        <div class="blue" style="font-size:21pt;line-height:1.3;margin-bottom:20pt">
          &mdash; but because single-account rule engines cannot express it.</div>
        <div class="dim" style="font-size:14.5pt;line-height:1.45;max-width:11in;margin-bottom:26pt">
          Three complementary views, fused under a monotonicity constraint into a
          calibrated score, cut false positives by
          {_pct(b['false_positive_reduction'], 2)} at unchanged recall &mdash; with
          explanations specific enough to file and containment cautious enough to
          automate.</div>
        <div class="stats">
          <div class="stat"><span class="v">{test_count()}</span><span class="l">tests passing</span></div>
          <div class="stat"><span class="v">{code_lines_short()}</span><span class="l">lines of code</span></div>
          <div class="stat"><span class="v">9</span><span class="l">layers, all running</span></div>
          <div class="stat"><span class="v">0</span><span class="l">external ML frameworks</span></div>
        </div>
        <div style="display:flex;gap:26pt;margin-top:26pt">{authors}</div>
        <div class="mono faint" style="font-size:12pt;margin-top:18pt">
          make setup &amp;&amp; make all &amp;&amp; make serve &nbsp;·&nbsp;
          github.com/deepakvish001/BOI-Hackathon-Prototype-</div>
      </div>'''))

    return (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<style>{CSS}</style></head><body>{"".join(S)}</body></html>')


def main() -> int:
    path = METRICS_DIR / "evaluation.json"
    if not path.exists():
        print("run `make evaluate` first", file=sys.stderr)
        return 1
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(build(json.loads(path.read_text())), encoding="utf-8")
    print(f"HTML  -> {OUT_HTML}  ({OUT_HTML.stat().st_size // 1024} KB)")

    subprocess.run([
        _chrome(), "--headless", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=20000",
        f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri(),
    ], check=True, capture_output=True)
    print(f"PDF   -> {OUT_PDF}  ({OUT_PDF.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
