"""Repair objective URL mismatches in aligned Kiswahili DOCX files.

The English policy document is authoritative for URL values. This utility
changes only URL substrings when an aligned paragraph contains the same number
of URLs; all translated prose and document structure remain untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from docx import Document


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENGLISH_DIR = BACKEND_DIR / "data" / "raw" / "KQ Data"
KISWAHILI_DIR = BACKEND_DIR / "data" / "translations" / "kiswahili"
AUDIT_PATH = KISWAHILI_DIR / "url-corrections.json"
URL_PATTERN = re.compile(r"https?://[^\s)]+")


def iter_paragraphs(container):
    """Yield body and table-cell paragraphs in deterministic order."""

    yield from container.paragraphs
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from iter_paragraphs(cell)


def replace_text(paragraph, text: str) -> None:
    """Replace all paragraph content, including embedded hyperlink nodes."""

    # ``paragraph.runs`` omits text stored inside hyperlink XML. Clearing the
    # paragraph is necessary here because the malformed URL itself is in such
    # a node. Paragraph-level style and numbering properties are retained.
    paragraph.clear()
    paragraph.add_run(text)


def correct_urls(english: str, kiswahili: str) -> tuple[str, list[dict]]:
    """Replace aligned Kiswahili URLs with authoritative English URLs."""

    english_urls = URL_PATTERN.findall(english)
    matches = list(URL_PATTERN.finditer(kiswahili))
    if len(english_urls) != len(matches):
        return kiswahili, []

    corrected = kiswahili
    changes = []
    for match, authoritative in reversed(
        list(zip(matches, english_urls, strict=True)),
    ):
        existing = match.group(0)
        if existing == authoritative:
            continue
        corrected = (
            corrected[: match.start()]
            + authoritative
            + corrected[match.end() :]
        )
        changes.append({"from": existing, "to": authoritative})

    return corrected, list(reversed(changes))


def main() -> int:
    """Apply URL-only corrections and write a transparent audit record."""

    report = {
        "method": "English-aligned URL-only correction",
        "documents": [],
    }

    for english_path in sorted(ENGLISH_DIR.glob("*.docx")):
        kiswahili_path = KISWAHILI_DIR / f"{english_path.stem} - SW.docx"
        english_doc = Document(english_path)
        kiswahili_doc = Document(kiswahili_path)
        english_paragraphs = list(iter_paragraphs(english_doc))
        kiswahili_paragraphs = list(iter_paragraphs(kiswahili_doc))

        if len(english_paragraphs) != len(kiswahili_paragraphs):
            raise ValueError(f"Structural mismatch: {english_path.name}")

        document_changes = []
        for index, (english, kiswahili) in enumerate(
            zip(english_paragraphs, kiswahili_paragraphs, strict=True),
        ):
            corrected, changes = correct_urls(english.text, kiswahili.text)
            if not changes:
                continue
            replace_text(kiswahili, corrected)
            document_changes.append(
                {"paragraph_index": index, "changes": changes},
            )

        if document_changes:
            kiswahili_doc.save(kiswahili_path)

        report["documents"].append(
            {
                "english": english_path.name,
                "kiswahili": kiswahili_path.name,
                "applied_corrections": document_changes,
            },
        )

    report["total_corrections"] = sum(
        len(document["applied_corrections"])
        for document in report["documents"]
    )
    AUDIT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"total_corrections": report["total_corrections"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
