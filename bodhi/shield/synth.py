"""Build syntactically valid APK fixtures for demos and tests.

The triage code parses a real binary ``AndroidManifest.xml`` string pool, so
testing it against a hand-waved mock would prove nothing. This module emits the
genuine little-endian AXML chunk layout (``ResChunk_header`` followed by a
``ResStringPool``), wraps it in a real ZIP alongside a DEX-shaped blob, and
therefore exercises the same code path a live sample would.

No executable Android bytecode is produced or required: the DEX blob is a
header plus string data, which is exactly the region the indicator miner reads.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

_RES_XML_TYPE = 0x0003
_RES_STRING_POOL_TYPE = 0x0001

DEFAULT_PERMISSIONS = (
    "android.permission.INTERNET",
    "android.permission.RECEIVE_SMS",
    "android.permission.READ_SMS",
    "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.BIND_ACCESSIBILITY_SERVICE",
    "android.permission.QUERY_ALL_PACKAGES",
    "android.permission.REQUEST_INSTALL_PACKAGES",
    "android.permission.READ_PHONE_STATE",
    "android.permission.RECEIVE_BOOT_COMPLETED",
)


def build_axml(strings: list[str]) -> bytes:
    """Serialise a UTF-16 AXML string pool inside a minimal XML chunk."""
    encoded: list[bytes] = []
    offsets: list[int] = []
    cursor = 0
    for s in strings:
        data = s.encode("utf-16-le")
        entry = struct.pack("<H", len(s)) + data + b"\x00\x00"
        offsets.append(cursor)
        cursor += len(entry)
        encoded.append(entry)

    pool_data = b"".join(encoded)
    pad = (-len(pool_data)) % 4
    pool_data += b"\x00" * pad

    strings_start = 28 + 4 * len(strings)
    chunk_size = strings_start + len(pool_data)
    header = struct.pack(
        "<HHIIIIII",
        _RES_STRING_POOL_TYPE, 28, chunk_size,
        len(strings), 0, 0, strings_start, 0,
    )
    pool = header + struct.pack(f"<{len(offsets)}I", *offsets) + pool_data
    file_size = 8 + len(pool)
    return struct.pack("<HHI", _RES_XML_TYPE, 8, file_size) + pool


def build_dex(vpas: list[str], urls: list[str], ips: list[str],
              ifsc: list[str], accounts: list[str],
              obfuscated: bool = True) -> bytes:
    """A DEX-shaped blob whose string region carries the indicators."""
    parts = [b"dex\n035\x00", b"\x00" * 104]
    for group in (vpas, urls, ips, ifsc, accounts):
        for value in group:
            parts.append(value.encode() + b"\x00")
    if obfuscated:
        # ProGuard-style single-letter class descriptors.
        for i in range(600):
            a = chr(ord("a") + i % 26)
            b = chr(ord("a") + (i // 26) % 26)
            parts.append(f"L{a}/{b};".encode() + b"\x00")
        for i in range(120):
            parts.append(f"Lcom/example/service/Handler{i};".encode() + b"\x00")
    else:
        for i in range(700):
            parts.append(f"Lcom/legit/app/Feature{i}Controller;".encode() + b"\x00")
    return b"".join(parts)


def build_sample_apk(
    path: str | Path,
    package: str = "com.secure.bankassist",
    permissions: tuple[str, ...] = DEFAULT_PERMISSIONS,
    vpas: tuple[str, ...] = ("collect9821@ybl", "payfast22@okaxis", "recv0071@paytm"),
    urls: tuple[str, ...] = ("http://c2-panel.example-bad.top/gate.php",
                             "https://cdn.example-bad.top/stage2.apk"),
    ips: tuple[str, ...] = ("103.145.22.71", "45.88.190.14"),
    ifsc: tuple[str, ...] = ("HDFC0001234", "SBIN0009876"),
    accounts: tuple[str, ...] = ("50100234567891", "38291002847361"),
    signed: bool = False,
    packed: bool = True,
    obfuscated: bool = True,
) -> Path:
    """Write a self-contained APK fixture and return its path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    manifest_strings = [
        package, "manifest", "uses-permission", "application", "activity",
        "android", "name", "label", *permissions,
        f"{package}.MainActivity", f"{package}.SmsReceiver",
        f"{package}.AccessibilityBridge",
    ]

    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", build_axml(manifest_strings))
        z.writestr("classes.dex", build_dex(list(vpas), list(urls), list(ips),
                                            list(ifsc), list(accounts), obfuscated))
        z.writestr("classes2.dex", build_dex([], [], [], [], [], obfuscated))
        z.writestr("resources.arsc", b"\x02\x00\x0c\x00" + b"\x00" * 64)
        z.writestr("res/layout/activity_main.xml", b"\x03\x00\x08\x00" + b"\x00" * 32)
        if packed:
            z.writestr("lib/arm64-v8a/libjiagu.so", b"\x7fELF" + b"\x00" * 512)
        z.writestr("lib/arm64-v8a/libnative.so", b"\x7fELF" + b"\x00" * 256)
        if signed:
            z.writestr("META-INF/CERT.RSA", b"\x30\x82" + b"\x00" * 512)
            z.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
    return p


def build_benign_apk(path: str | Path) -> Path:
    """A clean counterpart, so the scorer is shown to discriminate."""
    return build_sample_apk(
        path,
        package="com.acme.notes",
        permissions=("android.permission.INTERNET",
                     "android.permission.ACCESS_NETWORK_STATE"),
        vpas=(), urls=("https://api.acme-notes.example/v1/sync",),
        ips=(), ifsc=(), accounts=(),
        signed=True, packed=False, obfuscated=False,
    )


__all__ = ["build_axml", "build_dex", "build_sample_apk", "build_benign_apk",
           "DEFAULT_PERMISSIONS"]
