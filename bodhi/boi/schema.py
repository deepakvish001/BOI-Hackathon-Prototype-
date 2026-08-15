"""The bank's alert dataset: schema, feature grammar and leakage groups.

The organisers' data dictionary describes 3,923 predictors plus a target, one
row per **alert** (not per account). Almost all of the predictors are machine
generated from a small grammar:

    [aggregation] _ [CI?] _ [channel] _ [direction?] _ [measure] _ [window]

for example ``RA_CI_NON_CASH_CHQ_TXN_CR_L7_31D`` is "ratio of averages, of
customer-induced non-cash non-cheque credit transaction *counts*, last 7 days
versus last 31 days". Parsing that grammar rather than treating the columns as
3,923 opaque names is what lets us roll them into families, spot contradictions
between windows, and reason about which blocks are safe to use.

Two things in this schema matter more than any modelling choice:

**Resolution-status columns are label leakage.** ``FRAUD_SUSPECTED``,
``FALSE_POSITIVE``, ``OTHER_RESOLUTION`` and ``UNATTENDED`` record how an
analyst *closed* the alert. ``FRAUD_SUSPECTED`` is very nearly the target
written in a different column, and ``MIN/MAX_RESOLVE_DAYS`` only exist once the
alert has been worked. A model given these will show a near-perfect training
score and cannot be deployed, because at scoring time an open alert has no
resolution. They are quarantined by default; see :data:`LEAKAGE_COLUMNS`.

**The bank already down-selected 18 features.** The ``Bank_Finalized_Variables``
column marks them. That is domain knowledge worth more than any automatic
selector, so it is exposed as :data:`BANK_FINALIZED` and used as one of the
candidate feature sets the model selects between by cross-validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from bodhi.config import ROOT

DICTIONARY_PATH = ROOT / "data" / "schema" / "BOI_Data_Dictionary.xlsx"
SHEET = "Data_Dicitionary"          # (sic - the organisers' spelling)

TARGET = "FRAUD_TGT"

# --------------------------------------------------------------------------
# leakage
# --------------------------------------------------------------------------

#: How the alert was closed. Known only *after* investigation.
RESOLUTION_COLUMNS: tuple[str, ...] = (
    "FRAUD_SUSPECTED", "OTHER_RESOLUTION", "FALSE_POSITIVE", "UNATTENDED",
)

#: How long the investigation took. Same problem.
RESOLUTION_TIME_COLUMNS: tuple[str, ...] = (
    "MIN_RESOLVE_DAYS", "MAX_RESOLVE_DAYS",
)

#: Everything that must not reach the model in a deployable configuration.
LEAKAGE_COLUMNS: tuple[str, ...] = RESOLUTION_COLUMNS + RESOLUTION_TIME_COLUMNS

#: Identifier-ish columns: no predictive content, high cardinality, or a raw
#: date that would let the model memorise a time period.
NON_FEATURE_COLUMNS: tuple[str, ...] = ("ACCT_OPN_DATE",)

# --------------------------------------------------------------------------
# grammar
# --------------------------------------------------------------------------

#: Aggregation prefixes, longest first so ``D_TA_CI`` wins over ``D``.
AGGREGATIONS: tuple[str, ...] = (
    "D_TA_CI", "D_TA", "RA_CI", "DA_CI", "R_CI", "D_CI",
    "RA", "DA", "R", "D", "MIN", "MAX", "AVG", "SD", "TOT", "CNT",
)

AGGREGATION_MEANING = {
    "R": "ratio of totals between two windows",
    "RA": "ratio of averages between two windows",
    "D": "deviation between two windows",
    "DA": "deviation of averages between two windows",
    "D_TA": "deviation of the total from the average",
    "MIN": "minimum within the window",
    "MAX": "maximum within the window",
    "AVG": "average within the window",
    "SD": "standard deviation within the window",
    "TOT": "total within the window",
    "CNT": "count within the window",
    "RAW": "raw total within the window",
}

#: Subject tokens, longest first so NON_CASH_CHQ is never read as CASH and
#: UPI_XFER is never read as UPI. Derived from the dictionary rather than
#: guessed - the organisers use seventeen of these.
CHANNELS: tuple[str, ...] = (
    "NON_CASH_CHQ", "STDNG_INSTR", "FEES_CHRGS", "NET_BNKING", "MOB_BNKING",
    "ELEC_XFER", "POS_PYMT", "UPI_XFER", "MBNKING", "BBPS", "CASH", "CHQ",
    "LOAN", "GST", "UPI", "ATM", "APB", "POS",
)

CHANNEL_MEANING = {
    "CASH": "cash at branch",
    "CHQ": "cheque",
    "NON_CASH_CHQ": "everything that is neither cash nor cheque",
    "FEES_CHRGS": "fees and charges",
    "GST": "GST postings",
    "ELEC_XFER": "online transfer (IMPS + NEFT + RTGS)",
    "NET_BNKING": "internet banking",
    "MOB_BNKING": "mobile banking",
    "MBNKING": "mobile banking",
    "UPI": "UPI",
    "UPI_XFER": "UPI transfer",
    "ATM": "ATM",
    "POS_PYMT": "point-of-sale payment",
    "POS": "point of sale",
    "BBPS": "Bharat Bill Payment System",
    "APB": "Aadhaar Payment Bridge",
    "LOAN": "loan account postings",
    "STDNG_INSTR": "standing instruction",
}

#: ``CI`` = customer induced, ``BI`` = bank induced. The distinction matters:
#: a bank-induced posting (a fee, a GST debit) is not customer behaviour and
#: says nothing about whether an account is being used as a mule.
INDUCERS: tuple[str, ...] = ("CI", "BI")

DIRECTIONS: tuple[str, ...] = ("CR", "DB")
MEASURES: tuple[str, ...] = ("TXNS", "TXN", "AMT", "BAL")

#: Windows come in six spellings in this dictionary, not one:
#: ``L7D``, ``L7_14D``, ``14_31D`` (no L), ``7D_OCC``, ``14D_OC`` (truncated),
#: ``7DAYS``, ``7TO14DAYS_OCC``. An ``_OCC`` suffix means the value is a
#: deviation from the customer's *occupation segment* rather than an absolute.
WINDOW_RE = re.compile(
    r"_(?:L)?(\d+)(?:TO(\d+)|_(\d+))?D(?:AYS)?(_OCC?)?$"
)


@dataclass(frozen=True)
class ParsedFeature:
    """One column, decomposed into its grammatical parts."""

    name: str
    aggregation: str = "RAW"
    customer_induced: bool = False
    inducer: str | None = None        # CI (customer) / BI (bank) / None
    channel: str | None = None
    direction: str | None = None      # CR / DB / None (= total)
    measure: str | None = None        # TXN / AMT / BAL
    window: str | None = None         # L7D, L14D, L31D, L7_14D, 7D_OCC, ...
    window_days: tuple[int, ...] = ()
    occupation_relative: bool = False  # value is relative to the occupation peer group
    month_on_month: bool = False
    is_static: bool = False

    @property
    def family(self) -> str:
        """Everything except the window - the block a feature belongs to."""
        parts = [self.aggregation]
        if self.month_on_month:
            parts.append("MM")
        if self.inducer:
            parts.append(self.inducer)
        parts += [p for p in (self.channel, self.direction, self.measure) if p]
        if self.occupation_relative:
            parts.append("OCC")
        return "_".join(parts)

    @property
    def is_cross_window(self) -> bool:
        return len(self.window_days) == 2


def parse_feature(name: str) -> ParsedFeature:
    """Decompose a column name using the dictionary's grammar.

    Unrecognised names come back as ``is_static=True`` rather than raising:
    the demographic and alert-metadata columns do not follow the grammar, and
    a validation file may legitimately carry a column we have not seen.
    """
    original = name
    rest = name.upper()

    window = None
    window_days: tuple[int, ...] = ()
    occupation_relative = False
    m = WINDOW_RE.search(rest)
    if m:
        window = m.group(0).lstrip("_")
        window_days = tuple(int(g) for g in m.groups()[:3] if g)
        occupation_relative = bool(m.group(4))
        rest = rest[: m.start()].rstrip("_")

    aggregation = "RAW"
    inducer = None
    for agg in AGGREGATIONS:
        if rest.startswith(agg + "_"):
            aggregation = agg.replace("_CI", "")
            if agg.endswith("_CI"):
                inducer = "CI"
            rest = rest[len(agg) + 1:]
            break

    # ``MM_`` marks the month-on-month block; it modifies nothing else.
    month_on_month = rest.startswith("MM_")
    if month_on_month:
        rest = rest[3:]

    for ind in INDUCERS:
        if rest.startswith(ind + "_") or rest == ind:
            inducer = ind
            rest = rest[len(ind):].lstrip("_")
            break

    channel = None
    for ch in CHANNELS:
        if rest == ch or rest.startswith(ch + "_"):
            channel = ch
            rest = rest[len(ch):].strip("_")
            break

    direction = None
    measure = None
    for tok in (t for t in rest.split("_") if t):
        if tok in DIRECTIONS and direction is None:
            direction = tok
        elif tok in MEASURES and measure is None:
            measure = "TXN" if tok.startswith("TXN") else tok

    # Static = a demographic or alert-metadata column: no window and no
    # channel, so none of the transaction grammar applies.
    is_static = channel is None and window is None and measure is None
    return ParsedFeature(
        name=original, aggregation=aggregation,
        customer_induced=inducer == "CI", inducer=inducer,
        channel=channel, direction=direction, measure=measure,
        window=window, window_days=window_days,
        occupation_relative=occupation_relative, month_on_month=month_on_month,
        is_static=is_static,
    )


# --------------------------------------------------------------------------
# dictionary
# --------------------------------------------------------------------------


@dataclass
class DataDictionary:
    """The organisers' dictionary, plus everything derived from it."""

    table: pd.DataFrame

    @property
    def all_columns(self) -> list[str]:
        return self.table["Variable Name"].astype(str).tolist()

    @property
    def feature_columns(self) -> list[str]:
        """Every declared column except the target."""
        return [c for c in self.all_columns if c != TARGET]

    @property
    def bank_finalized(self) -> list[str]:
        sel = self.table[self.table["Bank_Finalized_Variables"].notna()]
        return [c for c in sel["Variable Name"].astype(str) if c != TARGET]

    @property
    def descriptions(self) -> dict[str, str]:
        return dict(zip(self.table["Variable Name"].astype(str),
                        self.table["Description"].astype(str)))

    def modelling_columns(self, allow_leakage: bool = False) -> list[str]:
        """Predictors a deployable model is allowed to see."""
        drop = set(NON_FEATURE_COLUMNS)
        if not allow_leakage:
            drop |= set(LEAKAGE_COLUMNS)
        return [c for c in self.feature_columns if c not in drop]

    def parsed(self) -> dict[str, ParsedFeature]:
        return {c: parse_feature(c) for c in self.feature_columns}

    def families(self) -> dict[str, list[str]]:
        """Feature blocks that differ only by observation window."""
        out: dict[str, list[str]] = {}
        for col, p in self.parsed().items():
            if p.is_static:
                continue
            out.setdefault(p.family, []).append(col)
        return out

    def summary(self) -> dict:
        parsed = self.parsed()
        chans = pd.Series([p.channel for p in parsed.values() if p.channel])
        aggs = pd.Series([p.aggregation for p in parsed.values()])
        return {
            "declared_columns": len(self.all_columns),
            "predictors": len(self.feature_columns),
            "modelling_predictors": len(self.modelling_columns()),
            "leakage_quarantined": len(LEAKAGE_COLUMNS),
            "bank_finalized": len(self.bank_finalized),
            "static_columns": sum(1 for p in parsed.values() if p.is_static),
            "families": len(self.families()),
            "channels": chans.value_counts().to_dict(),
            "aggregations": aggs.value_counts().to_dict(),
        }


@lru_cache(maxsize=4)
def load_dictionary(path: Path | None = None) -> DataDictionary:
    """Read and cache the organisers' data dictionary."""
    p = Path(path or DICTIONARY_PATH)
    if not p.exists():
        raise FileNotFoundError(
            f"data dictionary not found at {p}. It ships with the repository at "
            f"data/schema/BOI_Data_Dictionary.xlsx"
        )
    table = pd.read_excel(p, sheet_name=SHEET)
    table["Variable Name"] = table["Variable Name"].astype(str).str.strip()
    # The dictionary labels the target row's selection cell "Target Variable";
    # normalise so the finalized list contains only real predictors.
    return DataDictionary(table=table)


#: Convenience accessors used across the package.
def bank_finalized() -> list[str]:
    return load_dictionary().bank_finalized


def modelling_columns(allow_leakage: bool = False) -> list[str]:
    return load_dictionary().modelling_columns(allow_leakage)


__all__ = [
    "TARGET", "DICTIONARY_PATH", "LEAKAGE_COLUMNS", "RESOLUTION_COLUMNS",
    "RESOLUTION_TIME_COLUMNS", "NON_FEATURE_COLUMNS", "AGGREGATIONS",
    "AGGREGATION_MEANING", "CHANNELS", "CHANNEL_MEANING", "INDUCERS",
    "DIRECTIONS", "MEASURES",
    "ParsedFeature", "parse_feature", "DataDictionary", "load_dictionary",
    "bank_finalized", "modelling_columns",
]
