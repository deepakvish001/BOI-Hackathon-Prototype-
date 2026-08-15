"""The team list must appear, identically, in every submission artefact.

Authorship is easy to get wrong: a name is fixed in the deck, the report keeps
the old one, and the mismatch is only noticed after submission. Everything
renders from ``bodhi.config.TEAM``, and these tests assert that nothing has
drifted back to a hard-coded copy.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from bodhi.config import ROOT, TEAM, TEAM_NAME

DOCS = ROOT / "docs"
REPORT_DIR = DOCS / "report"


def test_team_entries_are_well_formed():
    assert len(TEAM) >= 1
    seen_names, seen_ids = set(), set()
    for member in TEAM:
        assert member.name.strip() == member.name and member.name
        # Enrolment numbers here are of the form 0246CS241037.
        assert re.fullmatch(r"\d{4}[A-Z]{2}\d{6}", member.enrolment), member.enrolment
        assert member.name not in seen_names, f"duplicate name {member.name}"
        assert member.enrolment not in seen_ids, f"duplicate id {member.enrolment}"
        seen_names.add(member.name)
        seen_ids.add(member.enrolment)


@pytest.mark.parametrize("doc", ["README.md", "SUBMISSION.md"])
def test_markdown_documents_list_the_team(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert TEAM_NAME in text
    for member in TEAM:
        assert member.name in text, f"{member.name} missing from {doc}"
        assert member.enrolment in text, f"{member.enrolment} missing from {doc}"


def test_latex_source_lists_the_team():
    tex = (REPORT_DIR / "bodhi_prototype.tex").read_text(encoding="utf-8")
    for member in TEAM:
        assert member.name in tex, f"{member.name} missing from the LaTeX source"
        assert member.enrolment in tex


def _pdf_text(path: Path) -> str:
    pytest.importorskip("fitz", reason="pymupdf not installed")
    import fitz

    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


@pytest.mark.parametrize("pdf", [
    "report/BODHI_Mule_Hunter_Prototype_Report.pdf",
    "BODHI_Mule_Hunter_Deck.pdf",
])
def test_rendered_pdfs_carry_the_team(pdf):
    path = DOCS / pdf
    if not path.exists():
        pytest.skip(f"{pdf} not built - run `make submission`")
    text = _pdf_text(path)
    for member in TEAM:
        assert member.name in text, f"{member.name} missing from {pdf}"
        assert member.enrolment in text, f"{member.enrolment} missing from {pdf}"


def _ooxml_text(path: Path) -> str:
    """Concatenate the text of every XML part in a .docx / .pptx."""
    out = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".xml") and ("document" in name or "slide" in name):
                out.append(re.sub(r"<[^>]+>", "", z.read(name).decode("utf-8")))
    return "\n".join(out)


@pytest.mark.parametrize("office", [
    "report/BODHI_Mule_Hunter_Prototype_Report.docx",
    "BODHI_Mule_Hunter_Deck.pptx",
])
def test_office_documents_carry_the_team(office):
    path = DOCS / office
    if not path.exists():
        pytest.skip(f"{office} not built - run `make submission`")
    text = _ooxml_text(path)
    for member in TEAM:
        assert member.name in text, f"{member.name} missing from {office}"
        assert member.enrolment in text, f"{member.enrolment} missing from {office}"


def test_deck_and_report_agree_on_headline_numbers():
    """Both documents render from evaluation.json; spot-check they match."""
    import json

    metrics = ROOT / "artifacts" / "metrics" / "evaluation.json"
    if not metrics.exists():
        pytest.skip("no evaluation.json - run `make evaluate`")
    m = json.loads(metrics.read_text())
    auc = f"{m['layers']['Fused (L7)']['roc_auc']:.4f}"

    for doc in (DOCS / "report/BODHI_Mule_Hunter_Prototype_Report.pdf",
                DOCS / "BODHI_Mule_Hunter_Deck.pdf"):
        if not doc.exists():
            pytest.skip(f"{doc.name} not built")
        assert auc in _pdf_text(doc), f"{auc} missing from {doc.name}"
