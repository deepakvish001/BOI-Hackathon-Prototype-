"""Repository facts the deck quotes about itself.

The closing slide claims a test count and a code size. Typing those means they
are wrong within a week, so they are counted from the tree at build time - the
same rule the rest of the deck follows for its metrics.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CODE_DIRS = ("bodhi", "dashboard", "scripts", "tests")
CODE_SUFFIXES = (".py", ".js", ".html", ".css")

_TEST_DEF = re.compile(r"^\s*def test_", re.MULTILINE)


def test_count() -> int:
    """Number of test functions, counted the way pytest collects them."""
    return sum(len(_TEST_DEF.findall(p.read_text(encoding="utf-8", errors="ignore")))
               for p in (ROOT / "tests").rglob("*.py"))


def code_lines() -> int:
    total = 0
    for d in CODE_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.suffix in CODE_SUFFIXES and "__pycache__" not in p.parts:
                total += p.read_text(encoding="utf-8", errors="ignore").count("\n")
    return total


def code_lines_short() -> str:
    n = code_lines()
    return f"~{n / 1000:.1f}k" if n >= 1000 else str(n)


__all__ = ["test_count", "code_lines", "code_lines_short"]
