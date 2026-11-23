"""Correct objective Kiswahili DOCX invariants from aligned English sources.

Only URLs, phone numbers, numeric values with units, and unambiguous policy
codes are changed. Natural-language translation is deliberately untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from docx import Document


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENGLISH_DIR = BACKEND_DIR / "data" / "raw" / "KQ Data"
KISWAHILI_DIR = BACKEND_DIR / "data" / "translations" / "kiswahili"
AUDIT_PATH = KISWAHILI_DIR / "invariant-corrections.json"

PATTERNS = {
    "url": re.compile(r"https?://[^\s)]+"),
    "phone": re.compile(r"\+\d(?:[\s-]?\d){7,}"),
    "number_or_measurement": re.compile(
        r"(?<![\w+])\d+(?:[.,]\d+)*(?:\s?(?:kg|kgs|cm|cms|lb|lbs|litres?|hours?|minutes?|days?|months?|years?|%))?",
        flags=re.IGNORECASE,
    ),
    "policy_code": re.compile(
        r"\b(?:SSR|EMD|IATA|DCS|MSC|PNR|TSA|KQ|WCHR|WCHS|WCHC|WCBD|WCBW|WCLB|WCMP|PETC|AVIH|UMNR|SPEQ)\b",
    ),
}


def iter_paragraphs(container):
    """Yield body and nested table-cell paragraphs in stable DOCX order."""

    yield from container.paragraphs
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from iter_paragraphs(cell)


def replace_text(paragraph, text: str) -> None:
    """Replace text while retaining the first run's paragraph formatting."""

    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
        return
    paragraph.add_run(text)


def positional_corrections(
    english: str,
    kiswahili: str,
) -> tuple[str, list[dict], list[dict]]:
    """Apply one-to-one replacements; flag count differences for review."""

    corrected = kiswahili
    applied: list[dict] = []
    unresolved: list[dict] = []

    for kind, pattern in PATTERNS.items():
        english_values = pattern.findall(english)
        kiswahili_values = pattern.findall(corrected)

        if english_values == kiswahili_values:
            continue
        if len(english_values) != len(kiswahili_values):
            # Different counts are normal across languages. This is a review
            # signal only, never proof of a translation defect.
            unresolved.append(
                {
                    "kind": kind,
                    "english": english_values,
                    "kiswahili": kiswahili_values,
                    "severity": "review_only",
                    "reason": (
                        "Token counts differ; verify semantic equivalence "
                        "only when this is an objective policy invariant."
                    ),
                },
            )
            continue

        # Replace matches from right to left so offsets remain stable.
        matches = list(pattern.finditer(corrected))
        for match, authoritative in reversed(
            list(zip(matches, english_values, strict=True)),
        ):
            existing = match.group(0)
            if existing == authoritative:
                continue
            corrected = (
                corrected[: match.start()]
                + authoritative
                + corrected[match.end() :]
            )
            applied.append(
                {
                    "kind": kind,
                    "from": existing,
                    "to": authoritative,
                },
            )

    return corrected, applied, unresolved


def main() -> int:
    report = {
        "method": "English-aligned objective invariant correction",
        "documents": [],
    }

    for english_path in sorted(ENGLISH_DIR.glob("*.docx")):
        kiswahili_path = (
            KISWAHILI_DIR
            / f"{english_path.stem} - SW.docx"
        )
        if not kiswahili_path.exists():
            raise FileNotFoundError(kiswahili_path)

        english_doc = Document(english_path)
        kiswahili_doc = Document(kiswahili_path)
        english_paragraphs = list(iter_paragraphs(english_doc))
        kiswahili_paragraphs = list(iter_paragraphs(kiswahili_doc))

        if len(english_paragraphs) != len(kiswahili_paragraphs):
            raise ValueError(
                f"Structural paragraph mismatch: {english_path.name}",
            )

        changes = []
        unresolved = []
        for index, (english, kiswahili) in enumerate(
            zip(
                english_paragraphs,
                kiswahili_paragraphs,
                strict=True,
            ),
        ):
            corrected, applied, ambiguous = positional_corrections(
                english.text,
                kiswahili.text,
            )
            if applied:
                replace_text(kiswahili, corrected)
                changes.append(
                    {
                        "paragraph_index": index,
                        "changes": applied,
                    },
                )
            if ambiguous:
                unresolved.append(
                    {
                        "paragraph_index": index,
                        "english_text": english.text,
                        "kiswahili_text": corrected,
                        "issues": ambiguous,
                    },
                )

        if changes:
            kiswahili_doc.save(kiswahili_path)

        report["documents"].append(
            {
                "english": english_path.name,
                "kiswahili": kiswahili_path.name,
                "applied_corrections": changes,
                "review_items": unresolved,
            },
        )

    report["totals"] = {
        "applied_paragraphs": sum(
            len(document["applied_corrections"])
            for document in report["documents"]
        ),
        "review_only_paragraphs": sum(
            len(document["review_items"])
            for document in report["documents"]
        ),
    }
    AUDIT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
