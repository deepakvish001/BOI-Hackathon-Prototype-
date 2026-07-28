# Model card — BODHI MULE HUNTER AI v1.0.0

## Intended use

**In scope.** Assisting a bank's AML / fraud-operations desk to prioritise which
accounts to investigate for money-mule activity, and to score in-flight
transactions inline. The system produces a ranked queue, an explanation, a
proposed action and a draft regulatory report.

**Out of scope.** Autonomous, unreviewed enforcement against a customer.
Credit decisions. Individual criminal liability. Any use where a human is not
positioned to review and reverse an action.

**Users.** Trained fraud analysts and AML officers. The narrative layer is
written for that audience; the raw scores are not intended for customer-facing
communication.

---

## Training data

Simulated. `bodhi/data/generator.py` produces 12,000 accounts and ~835,000
transactions over 120 days, with 1.26% mule accounts across 21 rings.

**Why simulated.** No bank can export account-level mule labels together with
device, IP and UPI linkage — the combination is simultaneously commercially
sensitive, personally identifying and legally restricted. No public dataset
carries it either. The alternative to simulation is not real data; it is no
evaluation at all.

**What the simulator reproduces.** Seven documented typologies: fan-in
aggregation, multi-hop layering, structuring/smurfing, dormant reactivation,
device-farm account rental, rapid cash-out, and malicious-APK harvest networks.

**What makes it non-trivial.** Four legitimate populations are planted
specifically to defeat naive detection:

| Population | Why it is dangerous | Count |
|---|---|---|
| Business Correspondent (Bank Mitra) agents | one handheld device legitimately operates 8–22 accounts, dispensing AePS cash all day — the exact fingerprint of a device farm | 302 |
| Community collectors | chit-fund and tuition organisers: burst fan-in from strangers, then forwarding ~90% within hours | 107 |
| Small businesses | heavy daily P2P fan-in from unrelated payers | 168 |
| Genuine dormancy breaks | NRI and student accounts waking after months | 168 |
| Near-threshold payers | repeated ₹44k–₹49.9k property and jewellery payments | ~96 |

Without these the models score ~0.999 AUC and the evaluation is worthless.

**Known distribution gaps.** The simulator does not model: cheque clearing,
cross-border remittance, merchant settlement cycles, festival seasonality,
customer complaints against the bank, or the correlated bursts that follow a
mass-phishing campaign. Real deployment would require recalibration.

---

## Model architecture

| Layer | Model | Parameters | Explanation method |
|---|---|---|---|
| 4 | XGBoost, 400 trees, depth 6 | ~1.5 M splits | exact TreeSHAP |
| 5 | GraphSAGE, 2 layers (64, 32), mean aggregator | ~11 K | GNNExplainer edge mask |
| 6 | TGN — GRU memory + learnable time encoding, 64 hidden, 64 events | ~30 K | per-event saliency |
| 7 | Non-negative calibrated blend | 6 | per-layer contribution |

Layers 5 and 6 are implemented in NumPy with hand-derived gradients, verified
against central finite differences at 2e-5 relative tolerance
(`tests/test_gradients.py`).

---

## Evaluation

Four disjoint folds: `train` (50%) fits the base models, `val` (15%) early-stops
them, `fuse` (15%) fits the Layer-7 blend, `test` (20%) is touched once.

### Headline

| Metric | Value |
|---|---|
| ROC-AUC (fused) | 0.9966 |
| PR-AUC (fused) | 0.9707 |
| Precision @1% alert budget | 0.992 |
| Recall @1% alert budget | 0.788 |
| Brier score | 0.00125 |
| Expected calibration error | 0.0047 |
| Inline decision latency p50 / p99 | 0.054 ms / 0.113 ms |

### Against the rule-based incumbent, at equal recall

| | Rule engine | BODHI |
|---|---|---|
| Alerts | 9,647 | 129 |
| True positives | 128 | 128 |
| False positives | 9,519 | 1 |
| Precision | 1.33% | 99.22% |

### Recall by typology — where it is weak

| Typology | Recall | Detected |
|---|---|---|
| **SMURFING** | **0.889** | 16/18 |
| **role:CASHOUT** | **0.921** | 35/38 |
| **FAN_IN_AGGREGATION** | **0.933** | 14/15 |
| LAYERING_CHAIN | 0.983 | 58/59 |
| role:COLLECTOR | 0.983 | 59/60 |
| APK_HARVEST | 1.000 | 15/15 |
| DEVICE_FARM | 1.000 | 31/31 |
| DORMANT_BURST | 1.000 | 13/13 |
| role:RELAY | 1.000 | 53/53 |

Structuring remains the weakest typology and this is expected: a smurf's individual
transfers are, by construction, indistinguishable from a genuine near-threshold
payment. Detection depends on the *aggregate* pattern, which only becomes
visible once enough transfers have accumulated. Terminal cash-out accounts are
the second weakest because they have short, thin histories — they receive once
and drain. Both are stated in `artifacts/metrics/evaluation.json` rather than
averaged away.

The false-positive rates on the planted decoy populations, measured at the
permissive review threshold of 40:

| Population | n | FP rate |
|---|---|---|
| legitimate dormant wake | 168 | 3.57% |
| community collector | 107 | 2.80% |
| ordinary retail | 10,504 | 0.77% |
| bc agent device cluster | 302 | 0.33% |
| small business fan in | 168 | 0.00% |
| merchant | 600 | 0.00% |

At the investigate threshold of 55, the entire bank yields 3 false positives at
93.4% recall.

### Out-of-time

100% recall on 26 mules from 3 rings that only activate in the
final quarter of the window — patterns the models never saw during training.

### Independence from the government feed

64% of detected mules (94 of 147) were never named in any
NCRP ticket, and recall on the 98 never-reported mules is 95.9%. The supervised layers
are structurally prevented from seeing ticket data (`INTEL_FEATURES` is disjoint
from `BEHAVIOUR_FEATURES`, asserted by test), so this is a measurement rather
than a claim.

---

## Fairness and disparate impact

**Not evaluated, and this is a real gap.** The simulator assigns a state and an
income band but no attribute that maps to a protected characteristic, so no
meaningful fairness audit is possible on this data.

Two specific risks a real deployment must measure before go-live:

1. **Minimum-KYC accounts** (`kyc_min_flag`) are a model feature and correlate
   in reality with lower-income and migrant customers. The feature is genuinely
   predictive — bought accounts are minimally KYC'd — but it will produce
   disparate alert rates.
2. **Shared-device and shared-IP features** disadvantage households sharing one
   phone and communities behind a single connection. The Business Correspondent
   decoy population exists precisely because this failure mode was anticipated,
   and the fan-out cap plus the ring flagged-share threshold are direct
   mitigations. They reduce the risk; they do not eliminate it.

Recommended pre-deployment work: alert-rate parity across state, income band and
KYC tier; a review path where an analyst can record "flagged for a
socio-economic reason, not a behavioural one"; and periodic re-examination of
whether `kyc_min_flag` still earns its place.

---

## Failure modes

| Failure | Consequence | Mitigation in code |
|---|---|---|
| Concept drift as typologies evolve | recall decays silently | `Casebook.feedback_labels()` returns investigator dispositions ready for retraining |
| Poisoned IoC feed | attacker gets innocent accounts frozen | intel is a separate fused channel, never a supervised feature; kill-switch requires ≥2 independent layers |
| Adversary spreads a ring across banks | graph is blind outside our data | consortium graph sharing is required; single-bank view is a stated limit |
| Adversary keeps velocity under thresholds | slow, patient mules evade | temporal layer is weakest here; the honest answer is that very slow mules are missed |
| Rule/feature bug silently zeroes a feature | scores shift without an error | feature hygiene tests assert finiteness and point-in-time equivalence |
| Model degradation after a bad retrain | mass false freezes | hourly automated-freeze budget caps the blast radius |

---

## Limitations, stated plainly

1. **Results are on simulated data.** They demonstrate that the architecture
   works and that it beats a rule engine on data containing realistic decoys.
   They are not a prediction of production performance.
2. **SHIELD does static analysis only.** The proposal describes a dynamic
   sandbox with Frida hooking and memory dumping. That needs an instrumented
   Android image and cannot ship in a self-contained prototype. What is
   implemented — manifest parsing, permission-combination scoring, DEX indicator
   mining, packer detection — is real and is the part that produces the
   financial intelligence the graph consumes.
3. **The graph layers are not real-time.** A full re-score of a 12,000-account
   graph takes ~38 s. The architecture is explicitly split: graph and temporal
   layers run on a schedule and cache a standing risk; the inline path combines
   those cached scores with the transaction's own attributes. A transaction
   involving an account whose neighbourhood changed since the last batch is
   scored on slightly stale structure.
4. **Single-institution view.** Rings that route through several banks are only
   partly visible. This is a data-sharing problem, not a modelling one.
5. **`neighbour_risk_mean` is the strongest single feature** (46% of total
   gain). It is legitimate label propagation and is cross-fitted to prevent
   leakage, but it means performance depends on having *some* confirmed labels.
   A cold start with zero known mules would perform materially worse.
6. **Fairness is unevaluated** (see above).
7. **The audit log is tamper-evident, not tamper-proof.** It needs an external
   anchor to become the latter.
8. **GNNExplainer explains less than it appears to.** For the highest-scoring
   accounts the edge mask reports a self-feature share near 100%: the GraphSAGE
   score is reproduced by the node's own feature vector with every edge deleted.
   That is a true statement, not a broken explainer — the feature vector already
   contains topology-derived features (PageRank, neighbour risk, clustering), so
   structure enters twice and message passing adds little on top for accounts
   that are already obviously bad. The ranked edge list remains useful for
   showing an investigator *which* relationships carry the most weight, but the
   fidelity figures should not be read as "the network structure caused this
   score". The clean fix is to drop topology features from the GNN's input and
   let message passing derive them, which is future work.

---

## Maintenance

- **Retraining.** Monthly, or when confirmed-fraud volume shifts by more than
  20%. `Casebook.feedback_labels()` supplies the supervision.
- **Monitoring.** Alert volume, precision on resolved cases, calibration drift
  (ECE), per-typology recall, and the false-positive rate on each decoy
  population.
- **Threshold review.** `ALERT_THRESHOLD` and `KILLSWITCH_THRESHOLD` in
  `bodhi/config.py` are operational decisions, not model constants, and should
  be reviewed against analyst capacity quarterly.

---

*Version 1.0.0 · every number reproducible with `make all`.*
