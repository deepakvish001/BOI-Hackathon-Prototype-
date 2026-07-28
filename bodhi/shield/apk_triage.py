"""Static triage of an Android package - no emulator, no external service.

Scope is deliberate. The proposal describes a full dynamic sandbox with Frida
hooking and memory dumping; that needs an instrumented Android image and cannot
be part of a self-contained prototype. What *is* implemented here is the part
that produces the financial intelligence the Mule Hunter graph consumes, and it
is implemented for real rather than mocked:

* the binary ``AndroidManifest.xml`` string pool is parsed to the spec, giving
  the declared permissions and the package name;
* ``classes*.dex`` is mined for UPI handles, bank account numbers, IFSC codes,
  C2 URLs, bare IP addresses and phone numbers;
* signing artefacts and packer signatures are checked;
* an interpretable risk score is assembled from permission combinations rather
  than from any single "dangerous" permission - READ_SMS alone is a messaging
  app, READ_SMS together with an accessibility service and an overlay is a
  banking trojan.

Everything a bank would actually route into fraud controls - the beneficiary
VPAs and accounts hardcoded in the sample - comes out of the DEX mining step.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# permission model
# --------------------------------------------------------------------------

#: permission -> (weight, why it matters)
DANGEROUS_PERMISSIONS: dict[str, tuple[float, str]] = {
    "android.permission.READ_SMS": (7, "Reads SMS - OTP interception"),
    "android.permission.RECEIVE_SMS": (8, "Receives SMS broadcasts - OTP theft"),
    "android.permission.SEND_SMS": (6, "Sends SMS - premium fraud / propagation"),
    "android.permission.SYSTEM_ALERT_WINDOW": (8, "Draws over other apps - overlay phishing"),
    "android.permission.BIND_ACCESSIBILITY_SERVICE": (
        10, "Accessibility service - keylogging and autonomous UI control"),
    "android.permission.REQUEST_INSTALL_PACKAGES": (6, "Installs further payloads"),
    "android.permission.READ_CONTACTS": (3, "Harvests contact list"),
    "android.permission.READ_PHONE_STATE": (3, "Device fingerprinting (IMEI/subscriber)"),
    "android.permission.RECORD_AUDIO": (5, "Microphone capture"),
    "android.permission.CAMERA": (3, "Camera capture"),
    "android.permission.ACCESS_FINE_LOCATION": (2, "Precise location tracking"),
    "android.permission.WRITE_EXTERNAL_STORAGE": (2, "Payload staging"),
    "android.permission.FOREGROUND_SERVICE": (1, "Persistence"),
    "android.permission.RECEIVE_BOOT_COMPLETED": (3, "Survives reboot"),
    "android.permission.DISABLE_KEYGUARD": (4, "Bypasses lock screen"),
    "android.permission.GET_ACCOUNTS": (3, "Enumerates device accounts"),
    "android.permission.QUERY_ALL_PACKAGES": (4, "Enumerates installed banking apps"),
    "android.permission.BIND_DEVICE_ADMIN": (7, "Device admin - resists removal"),
    "android.permission.CALL_PHONE": (3, "Places calls - vishing / call forwarding"),
    "android.permission.INTERNET": (0, "Network access (near-universal)"),
}

#: Combinations that are individually explicable but jointly diagnostic.
TOXIC_COMBINATIONS: tuple[tuple[tuple[str, ...], float, str], ...] = (
    (("android.permission.RECEIVE_SMS", "android.permission.SYSTEM_ALERT_WINDOW"),
     14, "OTP interception combined with overlay phishing - the classic "
         "credential-theft pair"),
    (("android.permission.BIND_ACCESSIBILITY_SERVICE",
      "android.permission.QUERY_ALL_PACKAGES"),
     12, "Enumerates banking apps and can drive their UI without the user"),
    (("android.permission.BIND_DEVICE_ADMIN", "android.permission.RECEIVE_SMS"),
     10, "Resists uninstallation while intercepting one-time passwords"),
    (("android.permission.REQUEST_INSTALL_PACKAGES",
      "android.permission.BIND_ACCESSIBILITY_SERVICE"),
     10, "Can silently install and grant a second-stage payload"),
)

# --------------------------------------------------------------------------
# extraction patterns
# --------------------------------------------------------------------------

# A UPI virtual payment address: handle@psp
VPA_RE = re.compile(rb"\b[a-zA-Z0-9._-]{3,64}@[a-zA-Z]{2,32}\b")
URL_RE = re.compile(rb"https?://[a-zA-Z0-9._~:/?#\[\]@!$&'()*+,;=%-]{4,200}")
IPV4_RE = re.compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IFSC_RE = re.compile(rb"\b[A-Z]{4}0[A-Z0-9]{6}\b")
ACCOUNT_RE = re.compile(rb"\b\d{11,18}\b")
PHONE_RE = re.compile(rb"\b(?:\+91|91)?[6-9]\d{9}\b")

#: e-mail providers that also match the VPA pattern; excluded to cut noise.
_EMAIL_HOSTS = {
    "gmail", "yahoo", "outlook", "hotmail", "example", "android", "google",
    "apache", "github", "googlemail", "live", "protonmail", "icloud",
}
#: Real UPI PSP handles.
_PSP_HANDLES = {
    "ybl", "okaxis", "oksbi", "okhdfcbank", "okicici", "paytm", "apl", "axl",
    "ibl", "upi", "airtel", "freecharge", "jupiteraxis", "fam", "naviaxis",
    "yapl", "abfspay", "idfcbank", "kotak", "barodampay", "sbi", "bodhipay",
}

PACKER_MARKERS = {
    b"libjiagu": "Jiagu (360) packer",
    b"libDexHelper": "DexProtector",
    b"libsecexe": "Bangcle / SecNeo",
    b"libapp_shell": "Ali packer",
    b"libDexGuard": "DexGuard",
    b"stub/StubApp": "Generic stub loader",
}


@dataclass
class APKReport:
    """Everything static analysis learned about one sample."""

    file_name: str
    size_bytes: int
    sha256: str
    md5: str
    is_valid_apk: bool
    package_name: str | None = None
    permissions: list[str] = field(default_factory=list)
    dangerous_permissions: list[dict[str, Any]] = field(default_factory=list)
    toxic_combinations: list[str] = field(default_factory=list)
    signed: bool = False
    signature_scheme: list[str] = field(default_factory=list)
    packers: list[str] = field(default_factory=list)
    obfuscation_ratio: float = 0.0
    dex_count: int = 0
    native_libs: list[str] = field(default_factory=list)
    upi_vpas: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    ifsc_codes: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    ip_addresses: list[str] = field(default_factory=list)
    phone_numbers: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    risk_band: str = "SAFE"
    findings: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @property
    def financial_indicators(self) -> int:
        return len(self.upi_vpas) + len(self.accounts) + len(self.ifsc_codes)


# --------------------------------------------------------------------------
# binary AXML string pool
# --------------------------------------------------------------------------

_RES_STRING_POOL_TYPE = 0x0001
_UTF8_FLAG = 1 << 8


def parse_axml_strings(data: bytes) -> list[str]:
    """Extract the string pool from a binary ``AndroidManifest.xml``.

    Only the string pool is decoded, not the full element tree. Permissions,
    the package name and every attribute value live in that pool, so this is
    enough for triage while remaining short enough to audit - and it degrades
    gracefully on the malformed manifests malware authors ship specifically to
    break naive parsers.
    """
    if len(data) < 8:
        return []
    out: list[str] = []
    # Walk top-level chunks until the string pool is found.
    pos = 8
    while pos + 8 <= len(data):
        try:
            c_type, header_size, c_size = struct.unpack_from("<HHI", data, pos)
        except struct.error:
            break
        if c_size <= 0 or pos + c_size > len(data):
            break
        if c_type == _RES_STRING_POOL_TYPE:
            out = _decode_string_pool(data[pos:pos + c_size])
            break
        pos += c_size
    return out


def _decode_string_pool(chunk: bytes) -> list[str]:
    if len(chunk) < 28:
        return []
    (_, _, _, string_count, _style_count, flags,
     strings_start, _styles_start) = struct.unpack_from("<HHIIIIII", chunk, 0)
    if string_count == 0 or string_count > 200_000:
        return []
    is_utf8 = bool(flags & _UTF8_FLAG)
    offsets_at = 28
    need = offsets_at + 4 * string_count
    if need > len(chunk):
        return []
    offsets = struct.unpack_from(f"<{string_count}I", chunk, offsets_at)

    out: list[str] = []
    for off in offsets:
        p = strings_start + off
        if p < 0 or p >= len(chunk):
            continue
        try:
            out.append(_decode_utf8_entry(chunk, p) if is_utf8
                       else _decode_utf16_entry(chunk, p))
        except (struct.error, UnicodeDecodeError, IndexError):
            continue
    return out


def _decode_utf8_entry(chunk: bytes, p: int) -> str:
    # Two length prefixes (character count, then byte count), each 1 or 2 bytes.
    def read_len(q: int) -> tuple[int, int]:
        n = chunk[q]
        if n & 0x80:
            return ((n & 0x7F) << 8) | chunk[q + 1], q + 2
        return n, q + 1

    _, p = read_len(p)
    byte_len, p = read_len(p)
    return chunk[p:p + byte_len].decode("utf-8", errors="replace")


def _decode_utf16_entry(chunk: bytes, p: int) -> str:
    n = struct.unpack_from("<H", chunk, p)[0]
    p += 2
    if n & 0x8000:
        n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", chunk, p)[0]
        p += 2
    return chunk[p:p + n * 2].decode("utf-16-le", errors="replace")


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------


def triage_apk(path: str | Path, max_dex_bytes: int = 48 * 1024 * 1024) -> APKReport:
    """Run static triage over an APK and return a structured report."""
    p = Path(path)
    raw = p.read_bytes()
    report = APKReport(
        file_name=p.name,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        md5=hashlib.md5(raw).hexdigest(),
        is_valid_apk=False,
    )

    if not zipfile.is_zipfile(p):
        report.errors.append("not a ZIP archive - cannot be an APK")
        report.risk_band = "UNKNOWN"
        return report

    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        report.is_valid_apk = "AndroidManifest.xml" in names
        if not report.is_valid_apk:
            report.errors.append("no AndroidManifest.xml - not a well-formed APK")

        # ---- manifest -------------------------------------------------
        if "AndroidManifest.xml" in names:
            try:
                strings = parse_axml_strings(z.read("AndroidManifest.xml"))
            except Exception as exc:  # pragma: no cover - corrupt input
                strings = []
                report.errors.append(f"manifest parse failed: {exc}")
            perms = sorted({s for s in strings
                            if s.startswith("android.permission.")
                            or s.startswith("com.android.")
                            or ".permission." in s})
            report.permissions = perms
            report.package_name = _guess_package(strings)

        # ---- signing --------------------------------------------------
        sig = [n for n in names if n.upper().startswith("META-INF/")
               and n.upper().endswith((".RSA", ".DSA", ".EC"))]
        report.signed = bool(sig)
        if sig:
            report.signature_scheme.append("v1 (JAR signing)")
        if not sig and report.is_valid_apk:
            report.findings.append({
                "code": "UNSIGNED",
                "detail": "No v1 signature block; the package cannot be attributed "
                          "to a publisher.",
            })

        # ---- native libraries & packers -------------------------------
        report.native_libs = sorted({n for n in names if n.startswith("lib/")
                                     and n.endswith(".so")})[:50]
        joined = "\n".join(names).encode()
        for marker, label in PACKER_MARKERS.items():
            if marker in joined:
                report.packers.append(label)

        # ---- DEX mining -----------------------------------------------
        dexes = [n for n in names if n.startswith("classes") and n.endswith(".dex")]
        report.dex_count = len(dexes)
        blob = bytearray()
        for name in dexes:
            if len(blob) >= max_dex_bytes:
                break
            try:
                blob += z.read(name)
            except Exception as exc:  # pragma: no cover
                report.errors.append(f"could not read {name}: {exc}")
        for marker, label in PACKER_MARKERS.items():
            if marker in blob and label not in report.packers:
                report.packers.append(label)

        _mine_indicators(bytes(blob), report)
        report.obfuscation_ratio = _obfuscation_ratio(bytes(blob))

    _score(report)
    return report


def _guess_package(strings: list[str]) -> str | None:
    """The package name is the shortest reverse-DNS string that is not a
    permission or a framework class."""
    candidates = [
        s for s in strings
        if re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,5}", s or "")
        and not s.startswith("android.")
        and not s.startswith("androidx.")
        and not s.startswith("java.")
        and not s.startswith("kotlin.")
    ]
    return min(candidates, key=len) if candidates else None


def _mine_indicators(blob: bytes, report: APKReport) -> None:
    """Pull financial and network indicators out of the DEX byte stream."""
    if not blob:
        return

    vpas: set[str] = set()
    for m in VPA_RE.findall(blob)[:20_000]:
        try:
            s = m.decode("ascii")
        except UnicodeDecodeError:
            continue
        handle = s.rsplit("@", 1)[-1].lower()
        if handle in _EMAIL_HOSTS or "." in handle:
            continue           # an e-mail address, not a VPA
        if handle in _PSP_HANDLES or (3 <= len(handle) <= 12 and handle.isalpha()):
            vpas.add(s)
    report.upi_vpas = sorted(vpas)[:200]

    report.urls = sorted({u.decode("ascii", "replace")
                          for u in URL_RE.findall(blob)[:20_000]})[:200]
    report.ip_addresses = sorted({
        ip for ip in (b.decode() for b in IPV4_RE.findall(blob)[:20_000])
        if all(0 <= int(o) <= 255 for o in ip.split("."))
        and not ip.startswith(("0.", "127.", "255."))
    })[:100]
    report.ifsc_codes = sorted({c.decode() for c in IFSC_RE.findall(blob)[:5_000]})[:100]
    report.phone_numbers = sorted({c.decode() for c in PHONE_RE.findall(blob)[:5_000]})[:100]

    # Bank account numbers only count when corroborated: an 11-18 digit run is
    # otherwise indistinguishable from a timestamp or a resource id.
    if report.ifsc_codes or report.upi_vpas:
        report.accounts = sorted({c.decode() for c in ACCOUNT_RE.findall(blob)[:5_000]})[:100]


def _obfuscation_ratio(blob: bytes) -> float:
    """Share of identifiers that are one or two characters long.

    ProGuard and DexGuard rename classes and methods to ``a``, ``b``, ``aa``.
    A normal app sits well under 0.25; a heavily obfuscated one runs past 0.6.
    """
    if not blob:
        return 0.0
    idents = re.findall(rb"L[a-zA-Z0-9/$_]{1,40};", blob[: 8 * 1024 * 1024])
    if len(idents) < 50:
        return 0.0
    short = sum(1 for i in idents
                if len(i.split(b"/")[-1].rstrip(b";")) <= 2)
    return round(short / len(idents), 4)


def _score(report: APKReport) -> None:
    """Assemble an interpretable 0-100 risk score."""
    score = 0.0
    findings = report.findings

    for perm in report.permissions:
        entry = DANGEROUS_PERMISSIONS.get(perm)
        if entry and entry[0] > 0:
            weight, why = entry
            score += weight
            report.dangerous_permissions.append(
                {"permission": perm, "weight": weight, "rationale": why})

    for combo, weight, why in TOXIC_COMBINATIONS:
        if all(c in report.permissions for c in combo):
            score += weight
            report.toxic_combinations.append(why)
            findings.append({"code": "TOXIC_COMBINATION", "detail": why})

    if report.packers:
        score += 10
        findings.append({"code": "PACKED",
                         "detail": f"Commercial packer detected: "
                                   f"{', '.join(report.packers)}."})
    if report.obfuscation_ratio > 0.55:
        score += 8
        findings.append({"code": "OBFUSCATED",
                         "detail": f"{report.obfuscation_ratio:.0%} of identifiers are "
                                   "one or two characters - heavy renaming."})
    if not report.signed and report.is_valid_apk:
        score += 6
    if report.upi_vpas:
        score += min(18, 6 + 2 * len(report.upi_vpas))
        findings.append({
            "code": "HARDCODED_VPA",
            "detail": f"{len(report.upi_vpas)} UPI handle(s) hardcoded in the "
                      "package - these are beneficiary accounts, streamed to "
                      "BODHI MULE HUNTER AI.",
        })
    if report.ifsc_codes:
        score += 8
        findings.append({"code": "HARDCODED_BANK_DETAILS",
                         "detail": f"{len(report.ifsc_codes)} IFSC code(s) embedded."})
    if report.ip_addresses:
        score += min(6, 2 * len(report.ip_addresses))
        findings.append({"code": "HARDCODED_C2",
                         "detail": f"{len(report.ip_addresses)} bare IP address(es) - "
                                   "candidate command-and-control endpoints."})

    report.risk_score = float(min(100.0, round(score, 2)))
    report.risk_band = (
        "CRITICAL" if report.risk_score >= 85 else
        "HIGH" if report.risk_score >= 65 else
        "MEDIUM" if report.risk_score >= 40 else "SAFE"
    )


__all__ = ["triage_apk", "APKReport", "parse_axml_strings", "DANGEROUS_PERMISSIONS",
           "TOXIC_COMBINATIONS"]
