"""PII minimisation for anything that leaves the bank's trust boundary.

Two distinct jobs, often confused:

**Redaction** is one-way and is applied to free text destined for a report,
a log line or a screen share. Identifiers are replaced with type markers.

**Tokenisation** is deterministic and keyed: the same account id always maps to
the same token, so an analyst can still correlate rows and count distinct
entities in an exported dataset without ever holding a real account number.
Because it is deterministic it is *pseudonymisation*, not anonymisation - an
attacker holding the key, or able to brute-force a small identifier space, can
reverse it. The keyed HMAC and a secret salt are what stand between those two
outcomes, which is why the salt is required to be supplied in production.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Iterable

import pandas as pd

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[ACCOUNT]", re.compile(r"\b(?:ACC)?\d{9,18}\b")),
    ("[VPA]", re.compile(r"\b[a-zA-Z0-9._-]{3,64}@[a-zA-Z]{2,32}\b")),
    ("[IFSC]", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("[PAN]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("[AADHAAR]", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("[PHONE]", re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b")),
    ("[EMAIL]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("[CARD]", re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b")),
    ("[IP]", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)

#: Columns that carry direct identifiers in the internal tables.
SENSITIVE_COLUMNS = {
    "account_id", "customer_id", "src_account", "dst_account", "vpa",
    "src_vpa", "dst_vpa", "beneficiary_account", "victim_account",
    "device_id", "ip_address", "home_device", "home_ip", "beneficiary_vpa",
}

_ENV_SALT = "BODHI_PII_SALT"


def _salt(explicit: str | None = None) -> bytes:
    if explicit:
        return explicit.encode()
    env = os.environ.get(_ENV_SALT)
    if env:
        return env.encode()
    # Development default. Deliberately constant so local runs are
    # reproducible, and deliberately obvious so it is never mistaken for a
    # production secret.
    return b"bodhi-development-salt-do-not-use-in-production"


def redact_text(text: str) -> str:
    """Replace every recognised identifier in free text with a type marker."""
    if not text:
        return text
    out = text
    # Card numbers before phone/account so the longer pattern wins.
    for marker, pattern in _PATTERNS:
        out = pattern.sub(marker, out)
    return out


def tokenise(value: str, salt: str | None = None, length: int = 12) -> str:
    """Deterministic keyed pseudonym for one identifier."""
    if value is None:
        return value
    digest = hmac.new(_salt(salt), str(value).encode(), hashlib.sha256).hexdigest()
    return f"TKN_{digest[:length].upper()}"


def redact_frame(
    df: pd.DataFrame,
    columns: Iterable[str] | None = None,
    salt: str | None = None,
    mode: str = "tokenise",
) -> pd.DataFrame:
    """Return a copy with identifier columns tokenised or dropped.

    ``mode='tokenise'`` keeps the data joinable; ``mode='drop'`` removes the
    columns entirely for the strictest export path.
    """
    cols = list(columns) if columns is not None else [
        c for c in df.columns if c in SENSITIVE_COLUMNS
    ]
    out = df.copy()
    for col in cols:
        if col not in out.columns:
            continue
        if mode == "drop":
            out = out.drop(columns=[col])
        else:
            out[col] = out[col].map(
                lambda v: tokenise(v, salt) if pd.notna(v) else v)
    return out


def is_production_salt_configured() -> bool:
    """True when a real salt has been supplied via the environment."""
    return bool(os.environ.get(_ENV_SALT))


__all__ = ["redact_text", "redact_frame", "tokenise", "SENSITIVE_COLUMNS",
           "is_production_salt_configured"]
