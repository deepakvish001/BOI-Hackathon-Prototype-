"""BODHI SHIELD AI - the APK side of the ecosystem.

Shield analyses a suspect Android package and hands the financial identifiers
it finds to the Mule Hunter graph. That hand-off is the point of the pair: by
the time a victim installs the trojan, the beneficiary accounts it was built to
pay have already been marked.
"""

from bodhi.shield.apk_triage import APKReport, DANGEROUS_PERMISSIONS, triage_apk
from bodhi.shield.ioc_bridge import ingest_iocs, iocs_from_report

__all__ = [
    "triage_apk",
    "APKReport",
    "DANGEROUS_PERMISSIONS",
    "iocs_from_report",
    "ingest_iocs",
]
