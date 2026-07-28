"""Tamper-evident audit trail.

An automated system that can freeze a customer's account has to be able to
prove, months later, exactly what it knew and when. This is a hash-chained
append-only log: each entry commits to the digest of its payload and to the
hash of the entry before it, so removing or editing any historical entry breaks
every hash after it and :meth:`AuditLog.verify` says so.

This is *tamper-evident*, not tamper-proof - a writer who controls the file can
recompute the whole chain. Making it tamper-proof needs an external anchor
(a WORM store, or periodic publication of the head hash), which is a deployment
decision rather than a code one. The head hash is exposed for exactly that
purpose.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhi.schemas import AuditEntry

GENESIS = "0" * 64


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


class AuditLog:
    """Append-only, hash-chained event log with optional file persistence."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.entries: list[AuditEntry] = []
        if self.path and self.path.exists():
            self._load()

    # -- writing ----------------------------------------------------------

    def append(self, actor: str, event: str, payload: Any) -> AuditEntry:
        prev = self.entries[-1].entry_hash if self.entries else GENESIS
        seq = len(self.entries)
        ts = datetime.now(timezone.utc)
        payload_digest = _digest(payload)
        entry_hash = hashlib.sha256(
            f"{seq}|{ts.isoformat()}|{actor}|{event}|{payload_digest}|{prev}".encode()
        ).hexdigest()
        entry = AuditEntry(seq=seq, timestamp=ts, actor=actor, event=event,
                           payload_digest=payload_digest, prev_hash=prev,
                           entry_hash=entry_hash)
        self.entries.append(entry)
        if self.path:
            self._append_file(entry, payload)
        return entry

    def _append_file(self, entry: AuditEntry, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = entry.model_dump(mode="json")
        record["payload"] = json.loads(json.dumps(payload, default=str))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            record.pop("payload", None)
            self.entries.append(AuditEntry(**record))

    # -- verification -----------------------------------------------------

    def verify(self) -> tuple[bool, int | None]:
        """Re-walk the chain. Returns ``(ok, first bad sequence number)``."""
        prev = GENESIS
        for i, e in enumerate(self.entries):
            if e.seq != i or e.prev_hash != prev:
                return False, i
            recomputed = hashlib.sha256(
                f"{e.seq}|{e.timestamp.isoformat()}|{e.actor}|{e.event}|"
                f"{e.payload_digest}|{e.prev_hash}".encode()
            ).hexdigest()
            if recomputed != e.entry_hash:
                return False, i
            prev = e.entry_hash
        return True, None

    @property
    def head(self) -> str:
        """Current chain head - publish this to make the log tamper-proof."""
        return self.entries[-1].entry_hash if self.entries else GENESIS

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in self.entries[-n:]]

    def __len__(self) -> int:
        return len(self.entries)


__all__ = ["AuditLog", "GENESIS"]
