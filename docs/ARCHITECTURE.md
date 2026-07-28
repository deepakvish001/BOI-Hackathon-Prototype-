# Architecture

This document explains *why* the system is built the way it is. The layer
numbering matches the platform proposal and the agent registry exposed at
`GET /api/agents`.

---

## The problem, stated precisely

A mule account is a legitimate account whose owner has rented, sold or been
tricked into lending it. Nothing about the account itself is anomalous — the
KYC is real, the customer exists, the payments clear. What is anomalous is the
*shape* of the money flowing through it and the *company it keeps*.

That has three consequences which drive every design decision here.

**Single-account features are not enough.** A collection mule receiving forty
payments from strangers in one afternoon is structurally identical to a
chit-fund organiser, a tuition-batch collector or a wedding-committee treasurer.
India has millions of the latter. Any system that alerts on fan-in alone will
bury its analysts. The simulator therefore plants all of those populations
deliberately (`bodhi/data/generator.py::_build_hard_negatives`) and the
evaluation reports the false-positive rate on each one separately.

**Aggregates destroy the signal.** "Received ₹4 lakh from 38 payers over the
month" describes both a shopkeeper and a mule. "Received ₹4 lakh from 38 payers
between 14:10 and 15:40, then withdrew ₹3.8 lakh in nineteen ATM visits starting
at 03:12" describes only one of them. Order matters, and no aggregate preserves
it.

**The government feed arrives too late to be the trigger.** NCRP tickets name a
beneficiary precisely — but hours to days after the money has already been
layered and cashed out. They are corroboration, not detection.

---

## Layer 1 — Ingestion

`bodhi/schemas.py`, `bodhi/feeds/`

Normalises four genuinely different input classes onto one schema:

| Input | Connector | Treatment |
|---|---|---|
| Cross-channel transactions (UPI, IMPS, NEFT, RTGS, AePS, ATM, card, wallet) | `schemas.Transaction` | the primary signal |
| Government cyber-fraud tickets (NCRP, I4C, 1930) | `feeds/ncrp.py` | corroboration only |
| APK-derived IoCs from BODHI SHIELD | `shield/ioc_bridge.py` | pre-emptive graph priming |
| Regulatory directives (RBI, NPCI, CERT-In, FIU-IND) | `feeds/regulatory.py` | four distinct behaviours |

Regulatory directives are deliberately *not* treated uniformly. A mule-account
list raises a score floor; a velocity limit changes a decision *threshold*, not
a score; a sanction is an absolute block; an advisory is prose recorded for the
audit trail and shown to investigators but never auto-applied, because a machine
cannot responsibly translate prose into an enforcement rule.

Everything carries a timestamp and is only ever applied from that instant
forward. `NCRPConnector.as_of()` and `RegulatoryConnector.as_of()` exist so this
cannot be forgotten.

---

## Layer 2 — Feature engineering

`bodhi/features/engineering.py` — 60 behavioural features per account.

Four rules the module obeys, each enforced by a test:

1. **No label leakage.** Nothing reads `is_fraud` or `is_mule`.
   `test_features_never_see_the_label` shuffles the labels and asserts the
   feature matrix is byte-identical.
2. **Intelligence stays out.** NCRP tickets name the mule; folding them into the
   supervised features would let the model memorise the answer key. They live in
   `build_intel_features` and are fused only at Layer 7.
3. **Point-in-time correctness.** Every builder takes `as_of`.
   `test_as_of_matches_a_manually_truncated_ledger` asserts that passing `as_of`
   is identical to truncating the ledger by hand.
4. **Vectorised.** 834,738 transactions → 12,000 feature vectors in ~4 s.

### The two features that were awkward to compute

**Rapid pass-through** — for each credit, how much left the account within the
next hour. This is naturally a per-account nested loop. Instead, debits are
sorted by `(account, timestamp)` and encoded as a single `int64` composite key
`account * 2e9 + ts`; one global `np.searchsorted` then answers the question for
every credit at once. Same trick for burst counting.

**Reciprocity** — the share of an account's payees who also pay it back. Genuine
social payment circles are two-way; a collection mule's edges are almost
perfectly one-directional. Computed with `np.isin` over composite keys rather
than a Python set loop.

---

## Layer 3 — The financial graph

`bodhi/graph/builder.py`

Accounts are nodes. Three edge types connect them:

- **MONEY** — aggregated transfers, log-scaled so one ₹5 lakh transfer does not
  drown out forty small ones. Kept directed for flow tracing, symmetrised for
  message passing.
- **DEVICE** — two accounts operated from the same handset. This is the edge
  that exposes account-rental farms and is completely invisible to any tabular
  model.
- **IP** — down-weighted heavily, because CGNAT means thousands of innocent
  customers share one address.

**Shared-identifier cliques are capped** (`MAX_SHARED_FANOUT = 25`). An
identifier behind more accounts than that is infrastructure — a CGNAT pool, a
banking kiosk — not evidence of collusion. Without the cap, one public IP
produces a 4,000-node clique and the graph layer becomes a false-positive
generator.

Stored as CSR arrays rather than a `networkx` object because GraphSAGE samples
neighbours millions of times per epoch.

### Ring extraction

`bodhi/graph/rings.py` — Louvain communities over the sub-graph induced by
high-risk accounts. Three guards matter:

- **Money edges do not expand the frontier.** Only device co-location pulls in
  an unflagged account. Otherwise every victim who paid a collector gets dragged
  into the ring and reported as a mule.
- **A community needs ≥35% independently-flagged members** to count as a ring.
  Without this, one flagged account inside a Business Correspondent's device
  cluster produced a "51-account device farm" of innocent village customers —
  observed during development, hence the guard.
- **Mean risk is computed over the flagged members**, not over everyone the
  device edge dragged in.

### Flow tracing

`trace_flows` returns **time-respecting** paths: each hop must occur after the
previous one and within a window. Money cannot be forwarded before it arrives,
so a path that ignores time is meaningless. The reported `value_retention` is
the giveaway — laundering chains leak 2–10% per hop as commission.

---

## Layers 4–6 — Three models that see different things

### Layer 4 — XGBoost

`bodhi/models/xgb_model.py`. The workhorse: disposes of ~99% of accounts
cheaply. Chosen over a neural tabular model for one reason — **exact TreeSHAP**.
`pred_contribs=True` gives true Shapley values in polynomial time, so the
attribution shown to an investigator is not a sampled approximation. It sums to
the model's margin (asserted in `test_shap_contributions_reconstruct_the_margin`)
and is stable across runs. That matters when the explanation ends up in a
Suspicious Transaction Report.

`scale_pos_weight` is **capped at 12**. Uncapped, on a 1.2% base rate, it
destroys calibration — and a mis-calibrated score cannot drive a kill-switch.

### Layer 5 — GraphSAGE

`bodhi/models/graphsage.py`. Mean-aggregator, two layers:

```
h_v^k = ReLU( W_self^k · h_v^(k-1) + W_neigh^k · MEAN_{u ∈ N(v)} h_u^(k-1) )
```

Neighbour sampling is a deterministic top-k fan-out on edge weight, applied when
the propagation matrix is built. Deterministic rather than random because an
investigator who re-opens a case tomorrow must see the same score.

The weights never index a node id, so the model is genuinely inductive — an
account opened this morning is scored from its neighbourhood alone.

### Layer 6 — Temporal graph network

`bodhi/models/temporal.py`. TGN's memory module is a GRU updated on every
interaction, with elapsed time injected through a learnable encoding. That is
what is implemented here, over each account's last 64 events, with
backpropagation through time written out by hand.

This is the layer that separates the shopkeeper from the mule: same daily
totals, same counterparty count, completely different ordering.

### Why NumPy and not PyTorch

Three reasons that matter for a bank prototype: the whole engine installs from
four wheels and runs on a laptop CPU; there is no GPU/CUDA surface to certify;
and every gradient is written out explicitly, so it can be audited.
`tests/test_gradients.py` checks each analytic gradient against central finite
differences at `2e-5` relative tolerance.

---

## Layer 7 — Fusion

`bodhi/models/fusion.py`

Blending happens in **log-odds space**. Sub-scores pile up against 0 and 1,
where a linear model in probability space cannot distinguish 0.990 from 0.9999
— yet that is a hundredfold difference in odds and exactly the region where
alert ranking is decided.

Two properties are non-negotiable:

**Monotonicity.** Blend weights are constrained non-negative, so the fused score
can never fall when a layer's evidence rises. This is not regularisation — an
unconstrained stacker on a small fold reliably learns "higher temporal score
means safer", which is indefensible to an investigator. A side effect is that
the blend can always fall back to "all weight on the best single layer", so
fusion cannot do much worse than its best input.

**Calibration.** Isotonic regression maps the blend onto observed frequencies.
Isotonic is a step function, so on a near-separable problem it collapses
thousands of distinct scores into a few plateaus and every account inside a
plateau becomes tied — measured as a 4-point AUC drop. Retaining 2% of the
(monotone) raw score breaks the ties without disturbing calibration.

The displayed 0–100 `risk_score` is a square-root warp of the probability, which
spreads out the crowded low-probability region. It is monotone, so ranking is
untouched — but calibration must be measured against `prob_mule`, not against
`risk_score / 100`.

### The fusion fold

Four folds, not three. A stacker fitted on the validation fold — the fold that
early-stopped its own inputs — inherits their optimism and came out *worse* than
the best single layer. `fuse` is a dedicated fold no base model has seen.

---

## Layer 8 — Explainability

`bodhi/explain/`

- **TreeSHAP** answers "which of this account's attributes drove the score".
- **GNNExplainer** (`gnn_explainer.py`) answers "which *relationships* drove
  it", by learning a soft mask over the edges of the two-hop neighbourhood,
  optimised so the masked sub-graph reproduces the prediction while staying
  sparse. The reported fidelity is the share of the original score retained.
- **Narrative** (`narrative.py`) maps every feature onto a sentence in the
  vocabulary of an AML desk. `rapid_passthrough_ratio = 0.94, SHAP +1.31` is
  useless to the officer who has to phone the customer; *"94% of every rupee
  credited left the account within 60 minutes — the account is a conduit, not a
  destination"* is not. The catalogue is data, not code, so the wording that
  reaches a regulator stays reviewable in one place. A test asserts no raw
  feature names leak into investigator-facing text.

---

## Layer 9 — Action

`bodhi/actions/`

Freezing an account is the most consequential thing this system can do. A
wrongly frozen account is somebody unable to pay for medicine, and no AUC
improvement justifies being casual about it. The kill-switch is therefore built
around constraints, not a threshold:

| Constraint | Rule |
|---|---|
| Proportionality | below 85, automation cannot freeze at all |
| Corroboration | a full freeze needs ≥2 independent layers agreeing |
| Reversibility | every action has a TTL and can be reverted |
| Rate limiting | bounded automated freezes per hour, capping blast radius |
| Protected accounts | salary/pension/benefit accounts always route to a human |

When a constraint fires the action is **downgraded**, never escalated, and
flagged `requires_human`. Every decision — including every refusal — is written
to the hash-chained audit log.

---

## Compliance

`bodhi/compliance/`

- **Audit chain** — each entry commits to its payload digest and the previous
  entry's hash. `verify()` re-walks the chain. This is tamper-*evident*, not
  tamper-proof: a writer controlling the file can recompute everything. Making
  it tamper-proof needs an external anchor (WORM storage, or publishing the head
  hash), which is a deployment decision. The head hash is exposed for exactly
  that purpose.
- **PII** — redaction is one-way, for text going to reports and logs.
  Tokenisation is deterministic keyed HMAC, so exported data stays joinable
  without holding real account numbers. It is *pseudonymisation*, not
  anonymisation, and the code says so.
- **STR drafting** — the evidence list shown to the investigator is the same
  list that populates the report's grounds-of-suspicion section, so what the
  model said and what the bank filed cannot drift apart. Drafts are marked
  `DRAFT_PENDING_HUMAN_REVIEW`.
- **CTR** — aggregated per account *per calendar day*, because the PMLA
  threshold applies to the daily aggregate. That is precisely why structuring
  defeats a per-transaction check.

---

## Performance

| Operation | Scale | Time |
|---|---|---|
| Simulate the bank | 12k accounts, 835k txns | 7 s |
| Feature engineering | 835k txns → 12k × 60 | ~4 s |
| Graph construction | 12k nodes, ~1M edges | ~3 s |
| Full training (all layers) | — | ~123 s |
| Full population re-score | 12k accounts | ~38 s |
| **Inline transaction decision** | one payment | **0.056 ms p50** |

The split matters: the graph and temporal layers run on a schedule and cache a
standing risk per account; the inline authorisation path combines those cached
scores with the transaction's own attributes. That is what makes a decision
feasible at UPI latencies — the graph is never traversed in the hot path.

---

## Deployment shape

```
core banking ──▶ Kafka ──▶ L1/L2 streaming features ──▶ feature store
                                                              │
              graph + GNN + TGN batch (scheduled)  ◀───────────┤
                          │                                    │
                    standing risk cache ──▶ inline decision ◀───┘
                          │                        │
                    investigator dashboard    ALLOW/REVIEW/HOLD/BLOCK
```

Everything in this repository is the middle two boxes plus the dashboard; the
Kafka and feature-store edges are where a production integration would attach.
