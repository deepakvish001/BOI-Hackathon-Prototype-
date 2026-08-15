"""Loading and aligning the bank's alert extract.

The validation file is held back by the organisers, so the loader's job is to
survive whatever arrives: a CSV or a parquet or an xlsx, columns in a different
order, columns missing, columns we have never seen, numbers stored as text with
thousands separators, and the target absent (as it will be at scoring time).

Alignment is against the published dictionary, not against the training file.
Aligning to the training file would let a validation extract with one extra
column silently shift everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from bodhi.boi.schema import (
    LEAKAGE_COLUMNS,
    NON_FEATURE_COLUMNS,
    TARGET,
    load_dictionary,
)

#: Columns the dictionary declares as categorical text.
CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "GENDER", "CUST_OCCP", "AREA_CATEGORY", "SEGMENTATION_CLASS",
    "PRODUCT_NAME", "ACCT_OPN_DAYS",
)


@dataclass
class AlignmentReport:
    """What had to be done to make the file usable. Printed, never hidden."""

    n_rows: int = 0
    declared: int = 0
    present: int = 0
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    coerced_to_numeric: list[str] = field(default_factory=list)
    all_null: list[str] = field(default_factory=list)
    constant: list[str] = field(default_factory=list)
    has_target: bool = False

    def summary(self) -> dict:
        return {
            "rows": self.n_rows,
            "declared_columns": self.declared,
            "present_columns": self.present,
            "missing_columns": len(self.missing),
            "unexpected_columns": len(self.unexpected),
            "coerced_to_numeric": len(self.coerced_to_numeric),
            "all_null_columns": len(self.all_null),
            "constant_columns": len(self.constant),
            "has_target": self.has_target,
        }

    def render(self) -> str:
        s = self.summary()
        lines = [
            f"  rows                {s['rows']:,}",
            f"  declared columns    {s['declared_columns']:,}",
            f"  present             {s['present_columns']:,}",
            f"  missing             {s['missing_columns']:,}"
            + (f"  e.g. {self.missing[:3]}" if self.missing else ""),
            f"  unexpected          {s['unexpected_columns']:,}"
            + (f"  e.g. {self.unexpected[:3]}" if self.unexpected else ""),
            f"  coerced to numeric  {s['coerced_to_numeric']:,}",
            f"  all-null            {s['all_null_columns']:,}",
            f"  constant            {s['constant_columns']:,}",
            f"  target present      {s['has_target']}",
        ]
        return "\n".join(lines)


def read_any(path: str | Path) -> pd.DataFrame:
    """Read a table from csv / tsv / parquet / xlsx without being told which."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower()
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(p)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(p)
    if suffix in (".tsv", ".tab"):
        return pd.read_csv(p, sep="\t", low_memory=False)
    return pd.read_csv(p, low_memory=False)


def _coerce_numeric(series: pd.Series) -> tuple[pd.Series, bool]:
    """Turn a text column into numbers when it plainly is one.

    Bank extracts routinely ship numbers as text with thousands separators,
    a trailing ``%``, or ``(1,234)`` for negatives. Leaving those as strings
    silently drops thousands of predictors.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series, False
    cleaned = (series.astype("string")
               .str.strip()
               .str.replace(",", "", regex=False)
               .str.replace("%", "", regex=False)
               .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
               .replace({"": None, "NULL": None, "null": None, "NA": None,
                         "N/A": None, "-": None, "#N/A": None}))
    converted = pd.to_numeric(cleaned, errors="coerce")
    # Only accept the conversion if it did not destroy the column.
    non_null = cleaned.notna().sum()
    if non_null and converted.notna().sum() >= 0.9 * non_null:
        return converted, True
    return series, False


def load_alerts(
    path: str | Path,
    *,
    allow_leakage: bool = False,
    drop_all_null: bool = True,
) -> tuple[pd.DataFrame, pd.Series | None, AlignmentReport]:
    """Load an alert extract and align it to the published dictionary.

    Returns ``(X, y, report)``. ``y`` is ``None`` when the file has no target,
    which is the normal case for a validation extract.
    """
    dd = load_dictionary()
    raw = read_any(path)
    raw.columns = [str(c).strip() for c in raw.columns]

    report = AlignmentReport(n_rows=len(raw), declared=len(dd.all_columns))

    y = None
    if TARGET in raw.columns:
        report.has_target = True
        y = pd.to_numeric(raw[TARGET], errors="coerce").fillna(0).astype(int)

    wanted = dd.modelling_columns(allow_leakage=allow_leakage)
    present = [c for c in wanted if c in raw.columns]
    report.missing = [c for c in wanted if c not in raw.columns]
    known = set(dd.all_columns)
    report.unexpected = [c for c in raw.columns if c not in known]
    report.present = len(present)

    X = raw.loc[:, present].copy()

    for col in X.columns:
        if col in CATEGORICAL_COLUMNS:
            X[col] = X[col].astype("string")
            continue
        converted, did = _coerce_numeric(X[col])
        X[col] = converted
        if did:
            report.coerced_to_numeric.append(col)

    # Any remaining text column that is not a declared categorical is treated
    # as one rather than dropped - an unexpected code column still carries
    # information.
    for col in X.columns:
        if X[col].dtype == object:
            X[col] = X[col].astype("string")

    numeric = X.select_dtypes(include=[np.number]).columns
    report.all_null = [c for c in numeric if X[c].isna().all()]
    report.constant = [c for c in numeric
                       if c not in report.all_null and X[c].nunique(dropna=True) <= 1]

    if drop_all_null and report.all_null:
        X = X.drop(columns=report.all_null)

    # Missing declared columns are re-inserted as all-NaN so that the feature
    # matrix has a stable shape between training and scoring. XGBoost treats
    # NaN as "no information", which is the correct semantics here.
    #
    # Added in one concat rather than a loop: inserting several thousand
    # columns one at a time fragments the block manager and turns a fast load
    # into a slow one.
    to_add = [c for c in report.missing
              if c not in X.columns and c not in report.all_null]
    if to_add:
        filler = pd.DataFrame(np.nan, index=X.index, columns=to_add, dtype="float32")
        X = pd.concat([X, filler], axis=1)

    X = X.reindex(columns=[c for c in wanted if c in X.columns])
    return X, y, report


def leakage_present(path: str | Path) -> list[str]:
    """Which quarantined columns does this file actually contain?"""
    raw = read_any(path)
    cols = {str(c).strip() for c in raw.columns}
    return [c for c in LEAKAGE_COLUMNS if c in cols]


__all__ = [
    "AlignmentReport", "CATEGORICAL_COLUMNS", "read_any", "load_alerts",
    "leakage_present", "NON_FEATURE_COLUMNS",
]
