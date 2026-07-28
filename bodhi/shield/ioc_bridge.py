"""The hand-off: APK findings become weighted nodes in the fraud graph.

This is the single most valuable link in the whole submission. A malicious APK
is analysed the day it appears on a Telegram channel; the beneficiary VPAs
baked into it are pushed straight into the Mule Hunter graph; and by the time
the first victim installs it, those beneficiary accounts are already carrying an
elevated intelligence score. Detection stops being retrospective.

Confidence is derived from the evidence, not asserted: an identifier pulled out
of a heavily-obfuscated, packed, unsigned sample that also declares an
accessibility service is worth far more than one found in a plain APK.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from bodhi.schemas import IoC
from bodhi.shield.apk_triage import APKReport


def _confidence(report: APKReport, base: float) -> float:
    """Scale an indicator's confidence by how malicious the carrier looks."""
    bump = 0.0
    if report.risk_band == "CRITICAL":
        bump += 0.15
    elif report.risk_band == "HIGH":
        bump += 0.08
    if report.packers:
        bump += 0.04
    if report.toxic_combinations:
        bump += 0.06
    if not report.signed:
        bump += 0.03
    return round(min(0.99, base + bump), 3)


def iocs_from_report(report: APKReport,
                     observed_at: datetime | None = None) -> list[IoC]:
    """Convert a triage report into indicators the graph can consume."""
    ts = observed_at or datetime.now(timezone.utc)
    family = _family_hint(report)
    out: list[IoC] = []

    def add(kind: str, value: str, base: float) -> None:
        out.append(IoC(
            ioc_id=f"SHIELD-{report.sha256[:12]}-{len(out):04d}",
            ioc_type=kind, value=value,
            confidence=_confidence(report, base),
            source_apk_sha256=report.sha256,
            malware_family=family,
            observed_at=ts,
        ))

    for vpa in report.upi_vpas:
        add("UPI_VPA", vpa, 0.72)
    for acct in report.accounts:
        add("ACCOUNT", acct, 0.55)
    for ip in report.ip_addresses:
        add("IP", ip, 0.5)
    for url in report.urls[:50]:
        add("URL", url, 0.45)
    for phone in report.phone_numbers:
        add("PHONE", phone, 0.5)
    add("SHA256", report.sha256, 0.9)
    return out


def _family_hint(report: APKReport) -> str:
    """Best-effort family label from the behavioural fingerprint."""
    perms = set(report.permissions)
    if {"android.permission.BIND_ACCESSIBILITY_SERVICE",
        "android.permission.RECEIVE_SMS"} <= perms:
        return "BankingTrojan.Accessibility"
    if "android.permission.SYSTEM_ALERT_WINDOW" in perms and report.upi_vpas:
        return "OverlayPhish.UPI"
    if report.packers:
        return "Packed.Unclassified"
    return "Unclassified"


def ingest_iocs(iocs: list[IoC], existing: pd.DataFrame | None = None) -> pd.DataFrame:
    """Merge new indicators into the IoC table, keeping the best confidence."""
    if not iocs:
        return existing if existing is not None else pd.DataFrame(
            columns=["ioc_id", "ioc_type", "value", "confidence",
                     "source_apk_sha256", "malware_family", "observed_at"])
    new = pd.DataFrame([{
        "ioc_id": i.ioc_id,
        "ioc_type": i.ioc_type,
        "value": i.value,
        "confidence": i.confidence,
        "source_apk_sha256": i.source_apk_sha256,
        "malware_family": i.malware_family,
        "observed_at": i.observed_at,
    } for i in iocs])
    if existing is None or not len(existing):
        return new
    merged = pd.concat([existing, new], ignore_index=True)
    merged = merged.sort_values("confidence", ascending=False)
    return merged.drop_duplicates(subset=["ioc_type", "value"], keep="first") \
                 .reset_index(drop=True)


__all__ = ["iocs_from_report", "ingest_iocs"]
