# The organisers' alert dataset

*"Your model will be evaluated based on the validation data set we have not
shared with you. Performance on the validation data set will have a lot of
weightages."*

This document covers the part of the submission that is scored against that
held-back file: `bodhi/boi/`, a pipeline built directly on the organisers'
published schema.

---

## What the dictionary actually describes

`Description.xlsx` (versioned here as
[`data/schema/BOI_Data_Dictionary.xlsx`](../data/schema/BOI_Data_Dictionary.xlsx))
declares **3,924 columns**: 3,923 predictors and the `FRAUD_TGT` label. One row
is one **transaction-monitoring alert**, not one account — which changes the
problem: the population is already pre-filtered by the bank's existing rules,
so the base rate is far higher than the ~1% of a raw account population, and
the job is *triage of alerts* rather than detection from scratch.

Almost every predictor is machine-generated from a compact grammar:

```
[aggregation] _ [CI|BI] _ [channel] _ [CR|DB] _ [TXN|AMT|BAL] _ [window]

RA_CI_NON_CASH_CHQ_TXN_CR_L7_31D
│  │   │            │   │   └── last 7 days vs last 31 days
│  │   │            │   └────── transaction count
│  │   │            └────────── credits only
│  │   └─────────────────────── non-cash, non-cheque
│  └─────────────────────────── customer induced (vs BI, bank induced)
└────────────────────────────── ratio of averages
```

`bodhi/boi/schema.py` parses that grammar rather than treating the names as
opaque. That is what makes it possible to roll 3,900 columns into ~1,300
families, to know that `NON_CASH_CHQ` must never be read as `CASH`, and to
distinguish customer behaviour from bank-induced postings like fees and GST.

| Dimension | Values found |
|---|---|
| Aggregations | `R`, `RA`, `D`, `DA`, `D_TA`, `MIN`, `MAX`, `AVG`, `TOT`, `CNT`, raw |
| Channels | 17, incl. `CASH`, `CHQ`, `NON_CASH_CHQ`, `UPI`, `ELEC_XFER`, `NET_BNKING`, `ATM`, `POS_PYMT`, `BBPS`, `APB`, `LOAN`, `STDNG_INSTR`, `FEES_CHRGS`, `GST` |
| Windows | `L7D`, `L14D`, `L31D`, `L7_14D`, `L14_31D`, `L7_31D`, plus `_OCC` occupation-relative variants |
| Static block | 33 columns: demographics, alert metadata, risk-level flags |

---

## The two findings that shape everything

### 1. Four columns leak the label

`FRAUD_SUSPECTED`, `FALSE_POSITIVE`, `OTHER_RESOLUTION` and `UNATTENDED` are
described in the dictionary as **"Resolution status flag"**. They record how an
analyst *closed* the alert. `MIN_RESOLVE_DAYS` and `MAX_RESOLVE_DAYS` are how
long that took.

An open alert — the only kind worth scoring — has none of them.

We measured what including them is worth, on a table with the organisers' exact
schema:

| | Selected strategy | CV PR-AUC | Held-out ROC-AUC |
|---|---|---|---|
| Resolution columns **quarantined** (default) | `bank_finalized` | **0.177** | 0.705 |
| Resolution columns **included** | `all` | **0.972** | 0.998 |

A 5.5× jump in PR-AUC, with `FRAUD_SUSPECTED` alone accounting for **33% of
total gain** and the six leakage columns taking the top five importance slots.
That is not a model. It is a lookup of the answer written in a different column.

`bodhi/boi/schema.py` quarantines them by default. `--allow-leakage` measures
the difference, and prints a warning while doing it. If the organisers'
validation file also carries these columns, a model that uses them will score
spectacularly and mean nothing; we would rather submit the honest number and
say so.

### 2. The bank already chose 18 features, and they win

The dictionary's `Bank_Finalized_Variables` column marks 18 predictors out of
3,923. That is domain knowledge from people who know this data.

The pipeline treats it as a hypothesis and tests it. Four feature strategies
compete under repeated stratified cross-validation:

| Strategy | Features | CV ROC-AUC | CV PR-AUC |
|---|---|---|---|
| **`bank_finalized`** | **18** | **0.705** | **0.172** |
| `bank_plus_engineered` | 2,028 | 0.634 | 0.149 |
| `auto_topk` | 131 | 0.631 | 0.125 |
| `all` | 5,926 | 0.604 | 0.105 |

The bank's eighteen beat all 5,926 columns, and beat automatic selection. With
a few hundred positives against thousands of predictors, that is exactly what
theory predicts — and it is why the pipeline *measures* rather than assumes.

The held-out slice, which nothing touched, scored **0.7046** against a CV
estimate of **0.7052**. A gap that small is the evidence that the selection
procedure is honest.

---

## How the pipeline avoids fooling itself

**Feature selection runs inside every fold.** Ranking 3,900 columns on the full
training set and then cross-validating the winners is the standard way to
manufacture a great score that evaporates on held-out data. Each fold ranks
using only its own training rows.

There is a test for this. `test_cross_validation_is_not_inflated_by_selection`
shuffles the target — destroying all signal — and asserts the reported AUC
stays near chance. A pipeline that selected globally returns well above 0.5 on
that input.

**Repeated stratified CV**, because with a few hundred positives a single
5-fold estimate moves by several AUC points depending on the seed.

**A seed ensemble** for the final model, so "we got lucky with `random_state`"
is not available as an objection.

**A holdout that nothing touches** — not selection, not early stopping, not
threshold tuning.

---

## Engineered features

The organisers have already done the aggregation work, so more raw ratios would
be noise. What the supplied set cannot express is information that lives
*across* its own columns, and that is all `bodhi/boi/features.py` adds
(everything prefixed `BX_`):

- **Family roll-ups** — mean / max / std / null-count across the 7, 14 and 31
  day versions of the same measurement, plus a short-vs-long **acceleration**
  ratio that says which direction the account is moving.
- **Channel concentration** — Herfindahl index, entropy and top-channel share
  over per-channel amounts. A mule funnels: in through UPI, out through cash.
  No supplied column can see that, because each is scoped to one channel.
- **Customer- vs bank-induced share** — an account whose activity is nearly all
  fees and GST postings is dormant in the way that matters.
- **Credit/debit balance** — a pass-through account debits almost exactly what
  it credits.
- **Missingness structure** — *which* blocks are null is informative in a bank
  extract; an account with no cheque history at all differs from one with a
  quiet cheque history.

On the stand-in data these did not beat the bank's eighteen. They are kept
because that comparison is data-dependent and will be re-run the moment the
real file lands.

---

## Robustness on submission day

The most likely way to fail is not the model — it is the file. The loader
(`bodhi/boi/dataset.py`) handles, and reports:

| Hazard | Handling |
|---|---|
| csv / tsv / parquet / xlsx | detected from the suffix |
| Columns in a different order | aligned against the dictionary, not the training file |
| Columns missing | re-inserted as all-NaN so the matrix shape is stable; reported |
| Unexpected extra columns | reported and ignored |
| Numbers shipped as text | `1,234.50`, `(2,000.00)`, `45%` coerced; reported |
| `NULL` / `N/A` / `-` / `#N/A` | treated as missing |
| Target column absent | expected — that is the validation case |
| All-null or constant columns | dropped, reported |

Every load prints an alignment report before anything else happens.

---

## Running it

```bash
# When the organisers release the training file:
python scripts/boi_train.py --train BOI_train.csv

# Score the held-back validation extract:
python scripts/boi_predict.py --model artifacts/boi \
       --input BOI_validation.csv --out submission.csv
```

To exercise the whole path today, against a table with the identical schema:

```bash
make boi-demo        # generate stand-in data, train, predict
make boi-leakage     # reproduce the leakage comparison above
```

`scripts/boi_predict.py` writes `FRAUD_TGT_PROBA` and `FRAUD_TGT_PRED`, carries
through an id column if you name one with `--id-column`, and accepts
`--threshold` to move the operating point once the organisers confirm their
metric.

---

## An honest statement about these numbers

**The organisers had not released the dataset when this was built** — only the
dictionary. Every number on this page was measured on
`bodhi/boi/synth.py`, which generates a table with the organisers' exact 3,924
columns and distributions implied by each column's grammar, with a deliberately
modest injected signal.

They demonstrate that the pipeline runs, that the methodology does not inflate
itself, and that the leakage trap is real. **They are not model performance and
must not be read as such.** When the real file arrives, `make boi-demo` becomes
`python scripts/boi_train.py --train <their file>` and every number on this
page is replaced by a measured one.

---

## Relationship to the rest of the submission

The main BODHI engine scores *accounts* from raw cross-channel transactions and
a graph. This track scores the bank's *pre-aggregated alerts*. They are
complementary, and they meet at the same output: a calibrated risk with an
explanation attached. In deployment the graph engine would generate the alerts
that this model then triages.
