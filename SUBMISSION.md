# Submission index — BODHI MULE HUNTER AI

CyberShield Hackathon 2026 · Bank of India · **Problem Statement 2**

### Team BODHI

| Name | Enrolment number |
|---|---|
| Akshay Tiwari | `0246CS241037` |
| Palak Vishwakarma | `0246AL241124` |
| Archi Singh Rajput | `0246CS240174` |
| Kartik Jain | `0246AL241094` |

---

## What to open first

| # | Deliverable | File | Notes |
|---|---|---|---|
| 1 | **Prototype report (PDF)** | [`docs/report/BODHI_Mule_Hunter_Prototype_Report.pdf`](docs/report/BODHI_Mule_Hunter_Prototype_Report.pdf) | 7 pages, two-column conference format |
| 2 | **Presentation (PPTX)** | [`docs/BODHI_Mule_Hunter_Deck.pptx`](docs/BODHI_Mule_Hunter_Deck.pptx) | 19 slides, 16:9, editable |
| 3 | **Presentation (PDF)** | [`docs/BODHI_Mule_Hunter_Deck.pdf`](docs/BODHI_Mule_Hunter_Deck.pdf) | same deck, for reviewers without PowerPoint |
| 4 | **Prototype (code)** | this repository | `make setup && make all && make serve` |

Supporting documents:

| Document | Purpose |
|---|---|
| [`README.md`](README.md) | overview, quick start, headline results |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | design decisions and the reasoning behind each |
| [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) | intended use, limitations, failure modes, fairness gaps |
| [`docs/BOI_TRACK.md`](docs/BOI_TRACK.md) | **the organisers' alert dataset** — schema, the leakage finding, how to run it on their file |
| [`docs/DEMO.md`](docs/DEMO.md) | five-minute judging walkthrough |
| [`artifacts/metrics/evaluation.json`](artifacts/metrics/evaluation.json) | every number quoted anywhere, in one file |

The report is also provided in the two templates the organisers supplied:
`BODHI_Mule_Hunter_Prototype_Report.docx` (Word/IEEE) and
`docs/report/bodhi_prototype.tex` (LaTeX, with `spconf.sty` and `IEEEbib.bst`).

---

## Headline results

12,000 accounts · 834,738 transactions · 151 mules (1.26%) · 21 rings

| Layer | ROC-AUC | PR-AUC | P@1% | R@1% |
|---|---|---|---|---|
| L4 XGBoost | 0.9946 | 0.9540 | 0.983 | 0.781 |
| L5 GraphSAGE | 0.9962 | 0.9481 | 0.983 | 0.781 |
| L6 Temporal GNN | 0.9628 | 0.8036 | 0.892 | 0.709 |
| Intelligence channel | 0.6883 | 0.3774 | 0.475 | 0.378 |
| **L7 Fused** | **0.9966** | **0.9707** | **0.992** | **0.788** |

**Against the rule engine it replaces, at identical recall (84.8%):**

| | Rule engine | BODHI |
|---|---|---|
| Alerts | 9,647 | **129** |
| False positives | 9,519 | **1** |
| Precision | 1.33% | **99.22%** |

→ 99.99% of false positives eliminated · 75× precision uplift
→ 100% recall on out-of-time rings · inline decision p50 **0.054 ms**
→ Brier 0.00125, ECE 0.0047 · 64% of detections never appeared in any NCRP ticket

---

## Running the prototype

```bash
make setup        # virtualenv + dependencies          (~1 min)
make all          # simulate → train → evaluate        (~12 min)
make serve        # dashboard + API on :8000
```

Short on time — this boots a smaller world and trains on first request (~2 min):

```bash
make setup && make serve
```

Other useful targets:

```bash
make demo         # narrated terminal walkthrough of a real detected ring
make test         # 85 tests, ~25 s
make submission   # rebuild the report and the deck from the measured metrics
```

`make submission` additionally installs `requirements-docs.txt` (python-pptx,
python-docx, Playwright) and renders the PDFs with headless Chromium. The
engine itself needs none of those.

---

## How the documents stay honest

Nothing quantitative in the report or the deck is typed by hand. Both builders
read `artifacts/metrics/evaluation.json`, which is written by
`scripts/evaluate.py`. Re-run `make evaluate && make submission` and every
document updates itself, so the paper, the slides and the code cannot drift
apart.

The dashboard screenshots in both documents are captured from the running
application by `scripts/capture_screenshots.py` — not mocked up. If the
dashboard were broken, the deck would show it.

---

## Problem statement coverage

| Requirement | Where it is implemented |
|---|---|
| AI/ML detection of suspicious transactions | Layers 4–7 (`bodhi/models/`) |
| AI/ML detection of mule accounts | account-level scoring + ring extraction (`bodhi/graph/rings.py`) |
| Ingest financial transactions | `bodhi/schemas.py`, 8 payment rails |
| Ingest fraud/transaction-monitoring alerts | `bodhi/baselines.py` rule outputs feed the same feature store |
| Ingest govt cyber-fraud alerts/tickets | `bodhi/feeds/ncrp.py` (NCRP / I4C / 1930) |
| Prevent circulation of fraudulent proceeds | `bodhi/actions/killswitch.py` + inline transaction decision |
| Real-time regulatory inputs/feeds | `bodhi/feeds/regulatory.py` (RBI / NPCI / CERT-In / FIU-IND) |
| Cross-channel bank data | UPI, IMPS, NEFT, RTGS, AePS, ATM, card, wallet |
| Explainability | `bodhi/explain/` — TreeSHAP, GNNExplainer, narrative |
| **Model for the organisers' own dataset** | `bodhi/boi/` — their 3,923-column alert schema, leakage-safe |
| Regulatory reporting | `bodhi/compliance/reports.py` — STR and CTR drafting |

---

## Known limitations

Stated in full in [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md). The important
ones: results are on simulated data (no bank can export real mule labels);
BODHI SHIELD performs static APK analysis only, not the dynamic sandbox
described in the Phase-1 proposal; the graph layers run on a schedule rather
than inline (~38 s full re-score); the system sees one institution; and
disparate impact has not been evaluated.
