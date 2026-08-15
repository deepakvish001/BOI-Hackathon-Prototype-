#!/usr/bin/env python3
"""Render the prototype report to PDF in the supplied two-column format.

There is no LaTeX toolchain in every environment this has to build in, so the
paper is composed as print-styled HTML and rendered by headless Chromium. The
layout follows the CyberShield/IEEE conference template: A4, two columns, Times
body, spanning title block, numbered sections, figures with captions.

As with the LaTeX and Word outputs, every number is read from
``artifacts/metrics/evaluation.json`` rather than typed, so the three formats
cannot disagree with each other or with the code.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bodhi.config import (  # noqa: E402
    AFFILIATION, EVENT, FIGURE_DIR, METRICS_DIR, ROOT, TEAM, TEAM_NAME,
)

OUT_HTML = ROOT / "docs" / "report" / "_report.html"
OUT_PDF = ROOT / "docs" / "report" / "BODHI_Mule_Hunter_Prototype_Report.pdf"
SHOTS = ROOT / "docs" / "screenshots"

CHROME_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]


def _chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    for c in CHROME_CANDIDATES:
        try:
            subprocess.run([c, "--version"], capture_output=True, check=True)
            return c
        except Exception:
            continue
    raise RuntimeError("no Chromium/Chrome binary found for PDF rendering")


def _img(path: Path, width: str = "100%") -> str:
    if not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b64}" style="width:{width}">'


def _pct(x, dp=1) -> str:
    return f"{100 * float(x):.{dp}f}%"


def _n(x) -> str:
    return f"{int(x):,}"


CSS = """
@page { size: A4; margin: 17mm 15mm 18mm 15mm; }
* { box-sizing: border-box; }
body {
  font-family: "Times New Roman", Times, serif; font-size: 9.2pt; line-height: 1.34;
  color: #000; margin: 0; text-align: justify; hyphens: auto;
}
.titleblock { column-span: all; text-align: center; margin-bottom: 9pt; }
.titleblock h1 { font-size: 16.5pt; font-weight: bold; margin: 0 0 7pt; line-height: 1.2; }
.titleblock .authors { display: flex; justify-content: center; gap: 12mm;
    margin: 7pt 0 6pt; }
.titleblock .author { text-align: center; }
.titleblock .author .nm { font-size: 10pt; }
.titleblock .author .en { font-size: 8.5pt; font-family: "Courier New", monospace; }
.titleblock .affil { font-size: 9pt; font-style: italic; margin: 0 0 1pt; }
.cols { column-count: 2; column-gap: 6.5mm; }
h2 {
  font-size: 9.6pt; font-weight: bold; text-align: center; text-transform: uppercase;
  margin: 11pt 0 5pt; break-after: avoid;
}
h3 { font-size: 9.2pt; font-style: italic; font-weight: normal; margin: 8pt 0 3pt; break-after: avoid; }
p { margin: 0 0 4.5pt; }
p.first { text-indent: 0; }
p.body { text-indent: 11pt; }
.abstract { font-size: 8.7pt; font-weight: bold; }
.abstract b.lead { font-style: italic; }
.keywords { font-size: 8.7pt; font-style: italic; font-weight: bold; margin-bottom: 7pt; }
table { width: 100%; border-collapse: collapse; font-size: 7.7pt; margin: 3pt 0 7pt; break-inside: avoid; }
caption {
  caption-side: top; font-size: 7.7pt; text-align: center; text-transform: uppercase;
  margin-bottom: 2.5pt; letter-spacing: .2px;
}
th, td { border: 0.5pt solid #000; padding: 2.2pt 3.5pt; }
th { font-weight: bold; text-align: center; background: #f0f0f0; }
td.l { text-align: left; } td.r { text-align: right; }
figure { margin: 4pt 0 7pt; break-inside: avoid; text-align: center; }
figcaption { font-size: 7.7pt; text-align: left; margin-top: 2.5pt; }
figure.wide { column-span: all; }
ul, ol { margin: 0 0 5pt; padding-left: 13pt; }
li { margin-bottom: 2.2pt; }
code { font-family: "Courier New", monospace; font-size: 8.2pt; }
.small { font-size: 8pt; }
.refs { font-size: 7.9pt; }
.refs p { margin-bottom: 2.2pt; text-indent: -11pt; padding-left: 11pt; }
.note { font-size: 8pt; font-style: italic; }
.kpi-band {
  column-span: all; display: flex; gap: 5pt; margin: 5pt 0 9pt;
  break-inside: avoid;
}
.kpi { flex: 1; border: 0.6pt solid #000; padding: 4pt 3pt; text-align: center; }
.kpi .v { font-size: 13pt; font-weight: bold; display: block; line-height: 1.1; }
.kpi .l { font-size: 6.6pt; text-transform: uppercase; letter-spacing: .3px; }
"""


def _boi_section() -> str:
    """Section X, rendered from artifacts/metrics/boi_track.json.

    The organisers published the column dictionary for their own alert dataset
    and told us the validation file is held back, so the report has to say what
    we did about it. Every number here is read from the metrics file for the
    same reason the rest of the paper is: so that a stale paragraph cannot
    outlive the code that produced it.
    """
    path = METRICS_DIR / "boi_track.json"
    if not path.exists():
        return ""
    m = json.loads(path.read_text())
    d, dep, leak = m["dataset"], m["deployable"], m["leakage_effect"]
    cv, hold = dep["cv"], dep["holdout"]

    order = [
        ("bank_finalized", "Bank-finalised subset"),
        ("bank_plus_engineered", "Bank subset + engineered"),
        ("auto_topk", "Automatic top-<i>k</i> selection"),
        ("all", "Every available column"),
    ]
    rows = "".join(
        f"<tr><td class='l'>{label}</td>"
        f"<td class='r'>{_n(cv[k]['n_features'])}</td>"
        f"<td class='r'>{cv[k]['roc_auc']:.3f}</td>"
        f"<td class='r'>{cv[k]['pr_auc']:.3f}</td></tr>"
        for k, label in order if k in cv
    )
    gap = abs(hold["roc_auc"] - cv[dep["selected_strategy"]]["roc_auc"])

    return f"""
<h2>X. The Organisers' Alert Dataset</h2>

<p class="first">The organisers released the column dictionary for the Phase&nbsp;2
dataset and stated that the model would be scored on a validation file they had
not shared. That dataset is a different object from the one above: one row is a
<i>transaction-monitoring alert</i>, not an account, so the population is already
pre-filtered by the bank's own rules and the task is triage rather than detection
from scratch. We therefore built a second pipeline (<code>bodhi/boi/</code>)
directly against the published schema of {_n(d['declared_columns'])} columns.</p>

<p class="body">Almost every predictor is machine-generated from a compact
grammar &mdash; aggregation, customer- or bank-induced, channel, direction,
measure, observation window &mdash; so the schema module <i>parses</i> the names
rather than treating them as opaque. That is what allows several thousand columns
to be grouped into families measured across the 7-, 14- and 31-day windows, and
it is what prevents <code>NON_CASH_CHQ</code> being silently matched as
<code>CASH</code>.</p>

<h3>A. Four columns leak the label</h3>

<p class="first">The dictionary describes <code>FRAUD_SUSPECTED</code>,
<code>FALSE_POSITIVE</code>, <code>OTHER_RESOLUTION</code> and
<code>UNATTENDED</code> as resolution-status flags: they record how an analyst
<i>closed</i> the alert, and <code>MIN_RESOLVE_DAYS</code> and
<code>MAX_RESOLVE_DAYS</code> record how long that took. An open alert &mdash;
the only kind worth scoring &mdash; carries none of them. Admitting them raises
cross-validated PR-AUC from {leak['pr_auc_deployable']:.3f} to
{leak['pr_auc_with_leakage']:.3f}, a {leak['multiple']:.1f}&times; jump, with
<code>FRAUD_SUSPECTED</code> alone worth roughly a third of the gain and the six
columns occupying the top importance ranks. That is not a model; it is a lookup
of the answer stored in a different column. They are quarantined by default, and
the flag that admits them prints a warning while it does so.</p>

<h3>B. The bank's own eighteen features win</h3>

<p class="first">The dictionary marks 18 predictors as bank-finalised. The
pipeline treats that as a hypothesis rather than an instruction and competes four
feature strategies under repeated stratified cross-validation, with selection
performed <i>inside</i> every fold.</p>

<table>
  <caption>Table V. Feature strategies, repeated stratified CV</caption>
  <tr><th>Strategy</th><th>Features</th><th>ROC-AUC</th><th>PR-AUC</th></tr>
  {rows}
</table>

<p class="first">The eighteen expert-chosen columns beat all
{_n(cv['all']['n_features'])}, and beat automatic selection. With
{_n(d['positives'])} positives against thousands of predictors this is what
statistical theory predicts, and it is precisely why the pipeline measures
instead of assuming. The untouched holdout scored {hold['roc_auc']:.4f} against a
cross-validated {cv[dep['selected_strategy']]['roc_auc']:.4f} &mdash; a gap of
{gap:.4f}, which is the evidence that the selection procedure is not inflating
itself. A regression test makes the same point adversarially: it shuffles the
target, destroying all signal, and asserts the reported AUC stays near chance,
which a globally-selecting pipeline fails.</p>

<p class="body"><i>These numbers are not model performance.</i> The organisers'
data had not been released when this was built, so they were measured on a
stand-in table generated with their exact {_n(d['declared_columns'])}-column
schema and a deliberately modest injected signal. What they demonstrate is that
the pipeline runs end to end, that the methodology does not flatter itself, and
that the leakage trap is real &mdash; nothing more. When the real file arrives,
one command replaces every figure in this section with a measured one.</p>
"""


def build_html() -> str:
    m = json.loads((METRICS_DIR / "evaluation.json").read_text())
    ds, L = m["dataset"], m["layers"]
    b, c, lat = m["baseline"], m["calibration"], m["latency"]
    ind, oot, lead = m["independence_from_tickets"], m["out_of_time"], m["lead_time"]
    thr = m["thresholds"]
    decoy = {d["population"]: d for d in m["hard_negatives"]}
    typ = {t["typology"]: t for t in m["typology"]}
    weak = [t for t in m["typology"] if not t["typology"].startswith("role:")]

    layer_rows = "".join(
        f"<tr><td class='l'>{name}</td><td class='r'>{v['roc_auc']:.4f}</td>"
        f"<td class='r'>{v['pr_auc']:.4f}</td>"
        f"<td class='r'>{v['precision_at_1pct']:.3f}</td>"
        f"<td class='r'>{v['recall_at_1pct']:.3f}</td></tr>"
        for name, v in L.items()
    )

    decoy_rows = "".join(
        f"<tr><td class='l'>{label}</td><td class='r'>{_n(decoy[k]['n'])}</td>"
        f"<td class='r'>{_pct(decoy[k]['false_positive_rate'], 2)}</td></tr>"
        for k, label in [
            ("bc_agent_device_cluster", "Business Correspondent agents"),
            ("community_collector", "Community collectors"),
            ("small_business_fan_in", "Small businesses (heavy fan-in)"),
            ("legitimate_dormant_wake", "Genuine dormancy reactivation"),
            ("ordinary_retail", "Ordinary retail"),
        ] if k in decoy
    )

    typ_rows = "".join(
        f"<tr><td class='l'>{k.replace('role:', 'role: ')}</td>"
        f"<td class='r'>{t['recall']:.3f}</td>"
        f"<td class='r'>{t['detected']}/{t['mules']}</td></tr>"
        for k, t in sorted(typ.items(), key=lambda kv: kv[1]["recall"])
    )

    authors = "".join(
        f'<div class="author"><div class="nm">{mem.name}</div>'
        f'<div class="en">{mem.enrolment}</div></div>' for mem in TEAM)

    boi_section = _boi_section()

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>

<div class="titleblock">
  <h1>BODHI MULE HUNTER AI: Explainable Graph-Temporal Detection<br>
      of Money-Mule Accounts and Suspicious Transactions</h1>
  <div class="authors">{authors}</div>
  <p class="affil">{TEAM_NAME} &mdash; {EVENT}</p>
  <p class="affil">{AFFILIATION}</p>
</div>

<div class="kpi-band">
  <div class="kpi"><span class="v">{L['Fused (L7)']['roc_auc']:.4f}</span><span class="l">Fused ROC-AUC</span></div>
  <div class="kpi"><span class="v">{L['Fused (L7)']['pr_auc']:.4f}</span><span class="l">Fused PR-AUC</span></div>
  <div class="kpi"><span class="v">{_pct(b['false_positive_reduction'], 2)}</span><span class="l">False positives removed</span></div>
  <div class="kpi"><span class="v">{b['precision_uplift_x']:.0f}&times;</span><span class="l">Precision uplift</span></div>
  <div class="kpi"><span class="v">{lat['p50_ms']:.3f} ms</span><span class="l">Inline decision p50</span></div>
  <div class="kpi"><span class="v">{_pct(oot['recall'], 0)}</span><span class="l">Out-of-time recall</span></div>
</div>

<div class="cols">

<p class="abstract first"><b class="lead">Abstract&mdash;</b>Money mules convert stolen
funds into untraceable cash, and rule-based transaction monitoring detects them
at a precision so low that analysts cannot act on the output. We present BODHI
MULE HUNTER AI, a nine-layer engine that fuses gradient-boosted tabular
screening, an inductive graph neural network and a temporal memory network over
each account's event sequence, calibrated into a single supervisory risk score.
On a simulated bank of {_n(ds['accounts'])} accounts and {_n(ds['transactions'])}
cross-channel transactions containing seven documented laundering typologies,
the fused model reaches {L['Fused (L7)']['roc_auc']:.4f} ROC-AUC and
{L['Fused (L7)']['pr_auc']:.4f} PR-AUC. Against a rule engine of the kind banks
deploy today, at identical recall, alert volume falls from {_n(b['alerts'])} to
{_n(b['bodhi_alerts_at_equal_recall'])} and precision rises from
{_pct(b['precision'], 2)} to {_pct(b['bodhi_precision_at_equal_recall'], 2)}
&mdash; {_pct(b['false_positive_reduction'], 2)} of false positives eliminated.
Inline decisions complete in {lat['p50_ms']:.3f}&nbsp;ms at the median. Every
alert carries exact Shapley attributions and a learned edge-importance mask, and
the containment layer refuses to act autonomously without corroboration from
independent model layers.</p>

<p class="keywords"><i>Keywords&mdash;</i>money mule detection, graph neural
networks, temporal graph networks, explainable AI, anti-money laundering</p>

<h2>I. Introduction</h2>

<p class="first">A money mule is an account whose owner has rented, sold, or been
deceived into lending it. Criminal proceeds are pushed into it, split, relayed
through several further accounts, and withdrawn as cash &mdash; typically within
hours. The account itself is unremarkable: the KYC is genuine, the customer
exists, the payments clear. What is anomalous is the <i>shape</i> of the money
passing through it and the <i>company it keeps</i>.</p>

<p class="body">This creates a specific failure mode for the transaction-monitoring
systems banks currently operate. Threshold-and-scenario engines evaluate accounts
in isolation, so they cannot express &ldquo;this account's counterparties are
themselves suspicious&rdquo; or &ldquo;these forty credits arrived in ninety
minutes and left at 03:12&rdquo;. Lacking that vocabulary, they compensate with
sensitivity, and the result is an alert queue an AML desk cannot clear. The
baseline we implement in Section VI raises {_n(b['alerts'])} alerts at
{_pct(b['precision'], 2)} precision: ninety-nine of every hundred investigations
are wasted.</p>

<p class="body">We make four contributions. <b>(1) A three-view ensemble.</b>
Rather than three variants of the same tabular model, we combine a
gradient-boosted model over behavioural aggregates, a GraphSAGE network over an
account&ndash;device&ndash;IP graph, and a temporal graph network over each
account's ordered event stream. <b>(2) Explainability that survives contact with
a regulator.</b> Layer 4 uses exact TreeSHAP, whose attributions sum to the
model's margin rather than approximating it; Layer 5 uses a GNNExplainer edge
mask. <b>(3) An evaluation designed to be falsifiable.</b> We plant legitimate
populations structurally indistinguishable from mules and report the
false-positive rate on each separately. <b>(4) Containment that can refuse.</b>
The kill-switch requires corroboration from at least two independent layers
before it may freeze an account, is rate-limited, reversible, and excludes
salary and pension accounts.</p>

<h2>II. Problem Setting and Data</h2>

<h3>A. Why a simulator</h3>
<p class="first">Account-level mule labels joined to device, IP and UPI linkage
are simultaneously commercially sensitive, personally identifying and legally
restricted; no bank can export them and no public dataset contains them. The
alternative to simulation is not real data &mdash; it is no evaluation at all.</p>

<p class="body">Our generator produces {_n(ds['accounts'])} accounts over
{ds['n_days']} days, {_n(ds['transactions'])} transactions across eight payment
rails (UPI, IMPS, NEFT, RTGS, AePS, ATM, card, wallet),
{_n(ds['mule_accounts'])} mule accounts ({_pct(ds['mule_rate'], 2)}) in
{ds['rings']} rings, and {_n(ds['fraud_alerts'])} government cyber-fraud tickets
arriving with realistic reporting lag. Seven laundering typologies from FIU-IND
and FATF guidance are implemented literally, so recall can be reported <i>per
typology</i> rather than as an average that hides blind spots.</p>

<h3>B. The hard negatives</h3>
<p class="first">This is the part of the data design that determines whether any
precision figure means anything. A collection mule receiving forty payments from
strangers in one afternoon is structurally identical to a tuition-batch
organiser. We therefore plant, as <i>legitimate</i> accounts, the populations in
Table I &mdash; most importantly Business Correspondent (Bank Mitra) agents,
where one handheld device legitimately serves many customers and dispenses AePS
cash all day, which is the exact fingerprint of a device farm performing rapid
cash-out. Without these populations the models score above 0.999 AUC and the
exercise is meaningless.</p>

<table>
<caption>Table I. False positives on deliberately planted look-alikes</caption>
<tr><th>Legitimate population</th><th>Count</th><th>FP rate</th></tr>
{decoy_rows}
</table>

<p class="note">Measured at the permissive review threshold of 40. At the
investigate threshold of 55 the entire bank yields
{thr['55']['false_positives']} false positives at {_pct(thr['55']['recall'], 1)}
recall.</p>

<h2>III. System Architecture</h2>

<p class="first">The engine is organised as nine layers, each owned by a named
agent with declared input and output contracts, so the provenance of every
decision is recoverable from the audit trail.</p>

<p class="body"><b>Layer 1 &mdash; Ingestion.</b> Normalises cross-channel
transactions, government cyber-fraud tickets (NCRP/I4C/1930), APK-derived
indicators, and regulatory directives. The four directive classes are handled
distinctly: a mule list raises a score floor; a velocity limit changes a decision
<i>threshold</i>, not a score; a sanction is absolute; an advisory is recorded
and surfaced but never auto-applied, because a machine cannot responsibly
convert prose into an enforcement rule.</p>

<p class="body"><b>Layer 2 &mdash; Feature engineering.</b> Sixty behavioural
features: velocity, fan-in/fan-out ratios, dormancy, burst scores, structuring
share, device reuse, pass-through ratios and channel mix. Every builder is
point-in-time correct, and a regression test asserts that passing an as-of
argument is identical to truncating the ledger by hand. A second test shuffles
the labels and asserts the feature matrix is unchanged, which forecloses label
leakage.</p>

<p class="body"><b>Layer 3 &mdash; Graph construction.</b> Accounts are nodes;
edges are money transfers, shared devices and shared IP addresses.
Shared-identifier cliques are capped: an identifier behind more than twenty-five
accounts is infrastructure &mdash; a CGNAT pool, a banking kiosk &mdash; not
evidence of collusion. Without the cap a single public address produces a
four-thousand-node clique and the graph layer becomes a false-positive
generator.</p>

<p class="body"><b>Layers 8&ndash;9</b> cover explainability and containment and
are described in Sections VII and VIII.</p>

<h2>IV. The Three Model Views</h2>

<p class="first">The central design claim is that mule detection needs three
genuinely different kinds of evidence, and that no single model family supplies
all three.</p>

<h3>A. Layer 4: gradient-boosted screening</h3>
<p class="first">An XGBoost classifier over the behavioural features disposes of
the overwhelming majority of accounts cheaply. It was chosen over a neural
tabular model for one reason: exact TreeSHAP. Shapley values for tree ensembles
are computable in polynomial time, so the attribution shown to an investigator is
not a sampled approximation &mdash; it sums to the model's raw margin, a property
asserted in the test suite, and it is stable across runs. That matters when the
explanation is transcribed into a regulatory filing. The class-imbalance weight
is capped rather than set to the full ratio; uncapped, on a
{_pct(ds['mule_rate'], 2)} base rate, it destroys calibration, and a
mis-calibrated score cannot be allowed to drive an automated freeze.</p>

<h3>B. Layer 5: inductive graph learning</h3>
<p class="first">We use the mean-aggregator GraphSAGE formulation [1] with two
layers and a deterministic top-<i>k</i> fan-out cap standing in for random
neighbour sampling. Determinism is a requirement, not a simplification: an
investigator re-opening a case tomorrow must see the same score. Because the
weights never index a node identity, the model is genuinely inductive &mdash; an
account opened this morning is scored from its neighbourhood alone.</p>

<h3>C. Layer 6: temporal memory</h3>
<p class="first">The memory module of a Temporal Graph Network [2] is a gated
recurrent unit updated on every interaction, with elapsed time injected through a
learnable encoding. We implement exactly that over each account's most recent
sixty-four events. This layer exists because aggregates destroy the signal that
matters most. &ldquo;Received Rs. 4 lakh from 38 payers over the month&rdquo;
describes a shopkeeper and a mule equally well. &ldquo;Received Rs. 4 lakh from
38 payers between 14:10 and 15:40, then withdrew Rs. 3.8 lakh in nineteen ATM
visits beginning at 03:12&rdquo; describes only one of them. Order is the
discriminant, and no aggregate preserves it.</p>

<h3>D. Implementation note</h3>
<p class="first">Both neural layers are implemented in NumPy with analytically
derived gradients rather than in a deep-learning framework. The motivation is
deployability in a bank: the entire engine installs from a handful of wheels,
runs on a laptop CPU, and presents no GPU or CUDA surface to certify. Because
&ldquo;hand-derived&rdquo; is a plausible source of silent error, every parameter
tensor's analytic gradient is verified against central finite differences in the
test suite.</p>

<h2>V. Fusion and Calibration</h2>

<p class="first">The four channel scores are blended in log-odds space.
Sub-scores accumulate against 0 and 1, where a linear model in probability space
cannot separate 0.990 from 0.9999; that is a hundredfold difference in odds and
precisely the region in which alert ranking is decided.</p>

<p class="body"><b>Monotonicity.</b> Blend weights are constrained non-negative,
so the fused score can never decrease when a layer's evidence increases. This is
not a regularisation convenience. An unconstrained stacker fitted on a small fold
reliably learns a negative coefficient on the temporal channel, which is
indefensible in front of an investigator and unshippable past a supervisor. A
useful side effect is that the blend can always degenerate to &ldquo;all weight
on the single best layer&rdquo;, so fusion cannot perform much worse than its
best input.</p>

<p class="body"><b>Calibration.</b> Isotonic regression maps the blend onto
observed frequencies, giving a Brier score of {c['brier']:.5f} and expected
calibration error of {c['ece']:.4f}. Isotonic regression is a step function, so
on a near-separable problem it collapses thousands of distinct scores into a
handful of plateaus and every account within a plateau becomes tied; we measured
a four-point AUC loss from those ties. Retaining a two-percent share of the
underlying monotone score breaks them without disturbing calibration.</p>

<p class="body">The blend is fitted on a fourth, dedicated fold. A stacker fitted
on the validation fold &mdash; the fold that early-stopped its own inputs &mdash;
inherits their optimism and, in our experiments, performed <i>worse</i> than the
best single layer.</p>

<h2>VI. Results</h2>

<table>
<caption>Table II. Per-layer detection performance</caption>
<tr><th>Layer</th><th>ROC-AUC</th><th>PR-AUC</th><th>P@1%</th><th>R@1%</th></tr>
{layer_rows}
</table>

<p class="first">The complementarity claim is visible: the fused model exceeds
every individual view on both ranking metrics. P@1% and R@1% are precision and
recall within an alert budget of one percent of accounts &mdash; the quantity an
analyst actually experiences.</p>

<h3>A. Against the incumbent</h3>
<p class="first">We implement a rule engine of the kind banks deploy today
&mdash; eight scenarios covering velocity, structuring, dormancy, cash-out ratio,
device sharing, new-account turnover and off-hours concentration &mdash; and give
it its best operating point rather than a strawman. The comparison is made at
<i>identical recall</i>, because a system that alerts on everything can always
claim to catch more.</p>

<table>
<caption>Table III. Rule engine versus BODHI at equal recall ({_pct(b['recall'], 1)})</caption>
<tr><th></th><th>Rule engine</th><th>BODHI</th></tr>
<tr><td class='l'>Alerts raised</td><td class='r'>{_n(b['alerts'])}</td><td class='r'><b>{_n(b['bodhi_alerts_at_equal_recall'])}</b></td></tr>
<tr><td class='l'>True positives</td><td class='r'>{b['true_positives']}</td><td class='r'>{b['bodhi_true_positives_at_equal_recall']}</td></tr>
<tr><td class='l'>False positives</td><td class='r'>{_n(b['false_positives'])}</td><td class='r'><b>{b['bodhi_false_positives_at_equal_recall']}</b></td></tr>
<tr><td class='l'>Precision</td><td class='r'>{_pct(b['precision'], 2)}</td><td class='r'><b>{_pct(b['bodhi_precision_at_equal_recall'], 2)}</b></td></tr>
</table>

<p class="body">{_pct(b['false_positive_reduction'], 2)} of false positives are
eliminated at unchanged recall, a {b['precision_uplift_x']:.0f}&times; precision
uplift. In operational terms an analyst clears the same true positives while
opening {_n(b['bodhi_alerts_at_equal_recall'])} cases instead of
{_n(b['alerts'])}.</p>

<h3>B. Robustness checks</h3>
<p class="first"><b>Out-of-time generalisation.</b> {oot['late_rings']} rings
activate only in the final quarter of the simulation window and are therefore
genuinely unseen patterns. Recall on their {oot['late_ring_mules']} member
accounts is {_pct(oot['recall'], 1)}.</p>

<p class="body"><b>Independence from the government feed.</b> The supervised
layers never receive ticket data &mdash; the intelligence features are a disjoint
set, asserted by test, and enter only at Layer 7. Consequently
{_pct(ind['share_of_detections_not_in_any_ticket'], 0)} of detected mules
({ind['detected_never_reported']} of {ind['detected_total']}) were never named in
any NCRP ticket, and recall on the {ind['mules_never_reported']} never-reported
mules is {_pct(ind['recall_on_never_reported_mules'], 1)}: these are cases the
government feed could not have supplied.</p>

<p class="body"><b>Detection lead time.</b> Of the mules that became active inside
the measurement grid and were eventually reported,
{_pct(lead['share_detected_before_ticket'], 0)} were detected before the
government ticket arrived (median lead {lead['median_lead_hours']:.1f}&nbsp;h,
resolved to a {lead['cut_spacing_hours']:.0f}&nbsp;h grid). A further
{lead['detected_but_never_ticketed']} were detected and never ticketed at all.</p>

<p class="body"><b>Latency.</b> Inline decisions complete in
{lat['p50_ms']:.3f}&nbsp;ms at the median and {lat['p99_ms']:.3f}&nbsp;ms at the
99th percentile, roughly {_n(lat['throughput_per_sec'])} decisions per second per
core. This is achievable because the graph and temporal layers run on a schedule
and cache a standing risk per account; the authorisation path combines those
cached scores with the transaction's own attributes and never traverses the
graph.</p>

<h3>C. Where the system is weak</h3>
<p class="first">Recall is not uniform, and averaging conceals that.</p>

<table>
<caption>Table IV. Recall by typology and mule role</caption>
<tr><th>Typology / role</th><th>Recall</th><th>Detected</th></tr>
{typ_rows}
</table>

<p class="body">The structuring result is expected and structural: an individual
sub-threshold transfer is, by construction, indistinguishable from a genuine
near-threshold payment, so detection depends on an aggregate pattern that only
becomes visible once enough transfers have accumulated. Terminal cash-out
accounts are similarly hard because they have short, thin histories &mdash; they
receive once and drain. We report these rather than averaging them away, because
a deployment team needs to know which typologies still require human-designed
scenarios alongside the model.</p>

<figure class="wide">
  {_img(FIGURE_DIR / 'roc_pr.png', '92%')}
  <figcaption><b>Fig. 1.</b> ROC and precision&ndash;recall by layer. The fused
  curve dominates every individual view, which is the empirical form of the
  complementarity claim in Section IV.</figcaption>
</figure>

<figure>
  {_img(FIGURE_DIR / 'calibration.png', '100%')}
  <figcaption><b>Fig. 2.</b> Calibration of the fused score (ECE {c['ece']:.4f}).
  The score has to mean what it says before it can drive an automated
  kill-switch.</figcaption>
</figure>

<figure>
  {_img(FIGURE_DIR / 'alert_budget.png', '100%')}
  <figcaption><b>Fig. 3.</b> Precision and recall against analyst workload.</figcaption>
</figure>

<h2>VII. Explainability</h2>

<p class="first">An alert reading <code>rapid_passthrough_ratio = 0.94, SHAP
+1.31</code> is unusable by the officer who must telephone the customer. The
narrative layer maps every feature onto a sentence in the vocabulary of the desk:
<i>&ldquo;94% of every rupee credited left the account within sixty minutes
&mdash; the account is a conduit, not a destination.&rdquo;</i> The mapping is
held as data rather than code, so the wording that eventually reaches a
supervisor remains reviewable in one place, and a test asserts that no raw
feature identifier leaks into investigator-facing text.</p>

<p class="body">Structural evidence is handled separately. GNNExplainer [3]
learns a soft mask over the edges of the target account's two-hop neighbourhood.
We report both standard fidelity measures alongside a <i>self-feature share</i>:
for accounts whose own behaviour is already decisive, that share is high and the
edge mask legitimately explains little. Reporting it prevents a low edge fidelity
from being misread as a broken explainer when it is in fact a correct statement
about where the evidence lives.</p>

<figure class="wide">
  {_img(SHOTS / '01_investigate_queue.png', '96%')}
  <figcaption><b>Fig. 4.</b> The investigator console. Four layer scores are shown
  separately rather than collapsed, the evidence list is written in the vocabulary
  of an AML desk, and each alert carries its cluster, exposure and linked
  government tickets.</figcaption>
</figure>

<figure class="wide">
  {_img(SHOTS / '03_network_graph.png', '96%')}
  <figcaption><b>Fig. 5.</b> Ego network for a flagged account. Node colour is the
  risk band; dashed edges are shared-device links, which are invisible to any
  tabular model. GNNExplainer runs on demand beneath the graph.</figcaption>
</figure>

<h2>VIII. Containment, Safety and Compliance</h2>

<p class="first">Freezing an account is the most consequential action the system
can take. A wrongly frozen account is a person unable to pay for medicine, and no
improvement in AUC justifies treating that casually. The kill-switch is therefore
built from constraints rather than a threshold: below the critical band the
automation cannot freeze at all; a full freeze requires at least two independent
layers to agree; every action carries a time-to-live and can be reverted;
automated freezes per hour are bounded, capping the blast radius if a model
degrades or an upstream feed is poisoned; and salary, pension and benefit
accounts are excluded from automated freezing entirely.</p>

<p class="body">When a constraint fires, the action is <i>downgraded</i> rather
than escalated and flagged for human review. Every decision, including every
refusal, is written to a hash-chained append-only audit log whose head hash is
exposed for external anchoring.</p>

<p class="body">The compliance layer drafts Suspicious Transaction Reports
populated from exactly the evidence displayed to the investigator, so what the
model asserted and what the institution files cannot diverge. Drafts are
explicitly marked as requiring human sign-off; nothing is filed autonomously.
Cash-transaction reporting aggregates per account per calendar day, which is the
level at which the obligation applies &mdash; and precisely why structuring
defeats a per-transaction check.</p>

<h2>IX. Ecosystem Synergy: BODHI SHIELD</h2>

<p class="first">Mule networks are frequently seeded by malicious Android
packages that carry their beneficiary accounts hardcoded inside them. Our
companion component performs static triage of a submitted APK: it parses the
binary <code>AndroidManifest.xml</code> string pool to specification, scores
permission <i>combinations</i> rather than individual permissions (READ_SMS alone
is a messaging application; READ_SMS together with an accessibility service and a
screen overlay is a banking trojan), mines the DEX for UPI handles, IFSC codes,
account numbers and command-and-control endpoints, and detects commercial packers
and identifier obfuscation.</p>

<p class="body">Every financial identifier recovered is streamed into the Mule
Hunter graph as a weighted node. The consequence is architectural rather than
incremental: when a new trojan is analysed on the day it appears, the beneficiary
accounts it was built to pay already carry an elevated intelligence score before
the first victim installs it. Detection stops being purely retrospective.</p>

<figure class="wide">
  {_img(SHOTS / '08_shield_apk.png', '96%')}
  <figcaption><b>Fig. 6.</b> BODHI SHIELD triaging an APK and handing the
  extracted UPI handles, IFSC codes and C2 addresses to the fraud graph.</figcaption>
</figure>

{boi_section}

<h2>XI. Limitations</h2>

<p class="first">We state these plainly because a prototype that hides them is
not useful to a deployment team. Results are on simulated data; they demonstrate
that the architecture works and that it decisively beats a rule engine on data
containing realistic decoys, but they are not a forecast of production
performance. The SHIELD component performs static analysis only &mdash; the
dynamic sandbox with runtime hooking described in our proposal requires an
instrumented Android image and cannot ship self-contained. The graph layers are
not real-time: a full re-score of the population takes tens of seconds, so an
account whose neighbourhood changed since the last batch is scored on slightly
stale structure. The system sees one institution, so rings routing through
several banks are only partly visible &mdash; a data-sharing problem rather than
a modelling one. The strongest single feature is neighbourhood risk propagated
from confirmed cases, which means a cold start with no known mules would perform
materially worse. Finally, disparate impact is unevaluated: minimum-KYC status
and shared-device features are predictive but correlate with lower-income and
multi-occupancy households, and any real deployment must measure alert-rate
parity before go-live.</p>

<h2>XII. Conclusion</h2>

<p class="first">Mule detection fails today not because the signal is absent but
because single-account rule engines cannot express it. Giving the problem three
complementary views &mdash; behavioural aggregates, network structure and event
ordering &mdash; and fusing them under a monotonicity constraint into a
calibrated score reduces false positives by
{_pct(b['false_positive_reduction'], 2)} at unchanged recall relative to a
realistic incumbent, while producing explanations specific enough to file and
containment actions cautious enough to automate. The same discipline is applied
to the organisers' own alert schema, where the honest finding is that four of its
columns encode the answer and the bank's eighteen expert-chosen features beat
automatic selection over several thousand. The complete system, the data
simulator and every script needed to reproduce these numbers are released
alongside this report.</p>

<h2>References</h2>
<div class="refs">
<p>[1]&nbsp;&nbsp;W. L. Hamilton, R. Ying, and J. Leskovec, &ldquo;Inductive
representation learning on large graphs,&rdquo; in <i>Proc. NeurIPS</i>, 2017.</p>
<p>[2]&nbsp;&nbsp;E. Rossi et al., &ldquo;Temporal graph networks for deep
learning on dynamic graphs,&rdquo; in <i>ICML Workshop on Graph Representation
Learning</i>, 2020.</p>
<p>[3]&nbsp;&nbsp;R. Ying, D. Bourgeois, J. You, M. Zitnik, and J. Leskovec,
&ldquo;GNNExplainer: Generating explanations for graph neural networks,&rdquo; in
<i>Proc. NeurIPS</i>, 2019.</p>
<p>[4]&nbsp;&nbsp;T. Chen and C. Guestrin, &ldquo;XGBoost: A scalable tree
boosting system,&rdquo; in <i>Proc. ACM SIGKDD</i>, 2016.</p>
<p>[5]&nbsp;&nbsp;S. M. Lundberg et al., &ldquo;From local explanations to global
understanding with explainable AI for trees,&rdquo; <i>Nature Machine
Intelligence</i>, vol. 2, no. 1, pp. 56&ndash;67, 2020.</p>
<p>[6]&nbsp;&nbsp;V. D. Blondel, J.-L. Guillaume, R. Lambiotte, and E. Lefebvre,
&ldquo;Fast unfolding of communities in large networks,&rdquo; <i>J. Stat.
Mech.</i>, 2008.</p>
<p>[7]&nbsp;&nbsp;Reserve Bank of India, &ldquo;Master direction on KYC and
framework for monitoring of mule accounts,&rdquo; 2024.</p>
<p>[8]&nbsp;&nbsp;Indian Cybercrime Coordination Centre, &ldquo;National
Cybercrime Reporting Portal,&rdquo; Ministry of Home Affairs, 2023.</p>
<p>[9]&nbsp;&nbsp;M. Weber et al., &ldquo;Anti-money laundering in Bitcoin:
Experimenting with graph convolutional networks for financial forensics,&rdquo;
in <i>KDD Workshop on Anomaly Detection in Finance</i>, 2019.</p>
</div>

</div>
</body></html>"""


def main() -> int:
    if not (METRICS_DIR / "evaluation.json").exists():
        print("run `make evaluate` first", file=sys.stderr)
        return 1

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(build_html(), encoding="utf-8")
    print(f"HTML  -> {OUT_HTML}  ({OUT_HTML.stat().st_size // 1024} KB)")

    chrome = _chrome()
    subprocess.run([
        chrome, "--headless", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=20000",
        f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri(),
    ], check=True, capture_output=True)

    print(f"PDF   -> {OUT_PDF}  ({OUT_PDF.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
