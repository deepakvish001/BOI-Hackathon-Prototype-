# Five-minute judging walkthrough

Two ways to run it. The terminal version narrates itself; the dashboard version
is what a real investigator would see.

```bash
make setup          # ~1 min
make all            # simulate → train → fixtures → evaluate (~12 min)
```

If you are short on time, `make setup && make serve` boots a smaller demo world
and trains on first request (~2 min), then serves the dashboard.

---

## Option A — terminal (2 minutes, self-narrating)

```bash
make demo
```

Walks through seven stages against a real detected ring:

1. **A cluster surfaces** — size, typology, internal turnover, and how many of
   its members really are mules according to ground truth.
2. **Why one account was flagged** — the four layer scores, the evidence list in
   plain English, and the generated case narrative.
3. **Following the money** — time-respecting multi-hop paths with elapsed time
   and the value retention that exposes each mule's commission.
4. **An inline decision** — a ₹95,000 payment into the flagged account, scored
   in well under a millisecond.
5. **Proportionate containment** — the kill-switch is *asked* for a full freeze
   and will often refuse, printing exactly which constraint stopped it.
6. **Regulatory output** — a draft Suspicious Transaction Report.
7. **The SHIELD hand-off** — an APK is triaged and its hardcoded UPI handles are
   streamed into the fraud graph.

---

## Option B — dashboard (5 minutes)

```bash
make serve      # http://localhost:8000
```

### 1. Investigate (60 s)

The alert queue is ranked by fused risk. Click the top alert.

- **Four layer bars** — note that they disagree. XGBoost sees the aggregates,
  GraphSAGE sees the ring, the TGN sees the ordering. Fusion is where they
  reconcile.
- **Evidence tab** — every reason is a sentence, not a feature name. Scroll to
  the cluster table: members, typology, shared devices, and which accounts are
  acting as collectors versus cash-out points.
- **Attribution tab** — exact TreeSHAP contributions in log-odds. These sum,
  with the base value, to the model's raw margin; they are not sampled.

### 2. The network (60 s)

Open the **Network** tab.

- Force-directed ego graph, coloured by risk band. Purple dashed edges are
  shared-device links — the ones a tabular model cannot see at all.
- Below the graph, **GNNExplainer** runs live: a learned mask over the two-hop
  neighbourhood, with the fidelity it retains. This answers *which relationships*
  drove the score, which SHAP structurally cannot.
- Click any node to jump to that account's case.

### 3. Money trail (30 s)

**Money trail** tab. Each path is time-respecting — every hop occurs after the
previous one. Watch **value retention**: a laundering chain leaks 2–10% per hop
as the handler's commission, which is a shape legitimate payment chains simply
do not have.

### 4. Containment (45 s)

**Actions** tab. Press **Full freeze** on a CRITICAL account.

Often the kill-switch will **refuse** and downgrade to a hold, printing the
constraint that stopped it — too few corroborating layers, below the automation
floor, rate limit exhausted, or a protected salary account. That refusal is the
point: an automated system that can freeze accounts must be able to say no, and
every refusal is written to the audit chain.

Then press **Draft STR** to see the regulatory report generated from exactly the
evidence displayed above.

### 5. Live scoring (30 s)

**Live scoring** tab → *Replay a 25-payment burst*. Watch the latency column:
inline decisions land in tens of microseconds, because the heavy layers already
cached a standing risk for both parties.

### 6. BODHI SHIELD (60 s)

**BODHI Shield** tab → *Analyse a bundled trojan sample* (or drag in any APK).

The manifest string pool is parsed for real, permission *combinations* are
scored, and the DEX is mined for UPI handles, IFSC codes and C2 addresses.
Scroll to **Hand-off**: the extracted identifiers are injected into the Mule
Hunter graph, and any that already correspond to accounts in this bank are
listed with their current risk.

This is the ecosystem argument in one screen — malware analysis priming fraud
controls *before* the first victim installs the app.

### 7. Pipeline & compliance (30 s)

- **Pipeline** — the nine agents, their inputs and outputs, and the measured
  performance table including the rule-engine comparison.
- **Compliance** — the audit chain with its verification status and head hash,
  plus cash transactions breaching the daily aggregate reporting threshold.

---

## What to look at if you have thirty seconds

```bash
cat artifacts/metrics/evaluation.json | python3 -m json.tool | head -40
```

The `headline` block, and then `baseline`: **9,647 rule-engine alerts at 1.33%
precision versus 132 alerts at 96.97% precision for identical recall.**

And `hard_negatives`, which is the number that makes the first one credible —
the false-positive rate on Business Correspondents, chit-fund collectors and
small businesses that were planted specifically to break the system.

---

## Reproducing every number

```bash
make evaluate       # rewrites artifacts/metrics/evaluation.json + figures
make test           # 83 tests including analytic-gradient verification
```

The whole pipeline is seeded; `make all` from a clean checkout reproduces the
figures in the README exactly.
