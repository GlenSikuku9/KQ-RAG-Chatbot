"""Apply researcher-authorised final dataset decisions to the private workbook.

This script changes annotation metadata only. It does not query retrieval, embed
questions, call providers, or populate human reviewer approval cells.
"""

from copy import copy
from datetime import date
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from openpyxl import load_workbook


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.append(str(BACKEND_DIR))

from scripts.finalize_reviewed_evaluation_dataset import (
    EVIDENCE_CHUNK_ALLOWLIST,
    NO_STATIC_EVIDENCE_GROUPS,
    WORKBOOK_PATH,
    active_parent_chunks,
    source_name,
)


STATUS = "AI-assisted validated and researcher-authorised"
AUTHORIZATION = "Researcher-authorised; individual human annotation approval waived"
AUTHORIZATION_DATE = date(2026, 11, 17).isoformat()

REFERENCE_ANSWER_UPDATES = {
    "dev-checkin": (
        "For airport check-in, the Check-in document says international passengers should "
        "complete check-in at least 2½ hours before departure and that counters generally close "
        "one hour before departure; Guangzhou counters close 90 minutes before departure. "
        "Conditions of Carriage article 6.1 instead gives airport-varying minimums of 60 minutes "
        "for Economy, 90 minutes for Business and 120 minutes for long-haul international flights. "
        "Because these provisions do not fully align, follow the stricter airport or itinerary "
        "instruction. Online check-in is separate: normally 48 hours to one hour before departure, "
        "or 24 hours to one hour for flights involving the United States."
    ),
    "dev-card-refund": (
        "KQ issues eligible credit/debit-card ticket refunds within 21–30 business days, excluding "
        "the bank's own processing time. Refunds depend on the ticketed fare rules and applicable "
        "fees or penalties."
    ),
}

LEAKAGE_RATIONALES = {
    "held-cabin": (
        "Retain. Historical questions test checked-baggage allowances. This group tests cabin "
        "baggage, a different table, parent chunk, piece/accessory rule, aggregate weight and "
        "dimensions. Topic similarity is disclosed but the policy information need is not reused."
    ),
    "held-rebook": (
        "Retain. Earlier booking-change questions concern voluntary modification. This group tests "
        "disruption-only self-rebooking, free use and automatic rebooking. The highest Kiswahili "
        "semantic match was to an unrelated wheelchair question and is treated as a false match."
    ),
}


def add_columns(sheet, headers: list[str]) -> dict[str, int]:
    existing = {cell.value: cell.column for cell in sheet[1] if cell.value}
    template = sheet.cell(1, max(1, sheet.max_column))
    for header in headers:
        if header in existing:
            continue
        column = sheet.max_column + 1
        cell = sheet.cell(1, column, header)
        cell.font = copy(template.font)
        cell.fill = copy(template.fill)
        cell.border = copy(template.border)
        cell.alignment = copy(template.alignment)
        cell.number_format = template.number_format
        sheet.column_dimensions[cell.column_letter].width = 34
        existing[header] = column
    return existing


def update_group_sheets(workbook) -> None:
    for sheet_name in ("Development", "Held-out"):
        sheet = workbook[sheet_name]
        columns = add_columns(sheet, [
            "Dataset validation status",
            "Researcher authorisation",
            "Researcher authorisation date",
            "Metric eligibility",
            "Final AI validation note",
        ])
        for row in range(2, sheet.max_row + 1):
            group_id = str(sheet.cell(row, 1).value or "")
            if not group_id:
                continue
            if group_id in REFERENCE_ANSWER_UPDATES:
                sheet.cell(row, 6, REFERENCE_ANSWER_UPDATES[group_id])
            if group_id == "dev-card-refund":
                sheet.cell(
                    row,
                    8,
                    "Refund Processing Times: eligible credit/debit card purchases within 21–30 business days, excluding the bank's own processing time. Refunds remain subject to ticketed fare rules and may attract fees or penalties.",
                )
            behavior_only = group_id in NO_STATIC_EVIDENCE_GROUPS
            metric_eligibility = (
                "Behaviour metrics only; exclude retrieval/context recall"
                if behavior_only
                else "Eligible for retrieval/context recall"
            )
            if group_id == "dev-checkin":
                metric_eligibility += "; exclude single-value answer scoring"
            note = "Validated against original DOCX and active indexed parents."
            if group_id in LEAKAGE_RATIONALES:
                note += " " + LEAKAGE_RATIONALES[group_id]
            sheet.cell(row, columns["Dataset validation status"], STATUS)
            sheet.cell(row, columns["Researcher authorisation"], AUTHORIZATION)
            sheet.cell(row, columns["Researcher authorisation date"], AUTHORIZATION_DATE)
            sheet.cell(row, columns["Metric eligibility"], metric_eligibility)
            sheet.cell(row, columns["Final AI validation note"], note)


def update_leakage_sheet(workbook) -> None:
    sheet = workbook["Leakage summary"]
    columns = add_columns(sheet, [
        "Researcher-authorised partition decision",
        "Final leakage rationale",
        "Dataset validation status",
    ])
    current_held_out = {
        str(workbook["Held-out"].cell(row, 1).value)
        for row in range(2, workbook["Held-out"].max_row + 1)
    }
    for row in range(2, sheet.max_row + 1):
        group_id = str(sheet.cell(row, 1).value or "")
        if group_id in current_held_out:
            decision = "Retain in held-out"
        elif group_id in {"held-special", "held-live"}:
            decision = "Retain in development"
        else:
            decision = "Exclude/retired"
        rationale = LEAKAGE_RATIONALES.get(
            group_id,
            "Researcher-authorised disposition follows the final partition and historical audit.",
        )
        sheet.cell(row, columns["Researcher-authorised partition decision"], decision)
        sheet.cell(row, columns["Final leakage rationale"], rationale)
        sheet.cell(row, columns["Dataset validation status"], STATUS)


def update_selected_chunks(workbook) -> None:
    sheet = workbook["Selected chunk index"]
    columns = {cell.value: cell.column for cell in sheet[1] if cell.value}
    existing = {
        (str(sheet.cell(row, 1).value), str(sheet.cell(row, 4).value))
        for row in range(2, sheet.max_row + 1)
    }
    allowed = EVIDENCE_CHUNK_ALLOWLIST["held-excess"]
    for row in range(2, sheet.max_row + 1):
        if sheet.cell(row, 1).value == "held-excess":
            key = (str(sheet.cell(row, 3).value), int(sheet.cell(row, 5).value))
            if key in allowed:
                decision = "AI-assisted validated mandatory evidence"
                comment = "Direct general excess-charge, bulky-baggage or heavy-bag evidence; researcher-authorised."
            else:
                decision = "Excluded from general excess-baggage evidence"
                comment = "Sports-, media-, cabin- or equipment-specific text is not valid evidence for the general overweight-bag rule."
            sheet.cell(row, columns["AI evidence review"], decision)
            sheet.cell(row, columns["AI reviewer comments"], comment)

    parents, _ = active_parent_chunks()
    for chunk in sorted(parents, key=lambda item: (source_name(item), item.chunk_index)):
        document = source_name(chunk)
        if (document, chunk.chunk_index) not in allowed:
            continue
        key = ("held-excess", chunk.chunk_id)
        if key in existing:
            continue
        sheet.append([
            "held-excess",
            "held_out",
            document,
            chunk.chunk_id,
            chunk.chunk_index,
            chunk.section,
            chunk.source_url,
            chunk.text,
            None,
            None,
            "AI-assisted validated mandatory evidence",
            "Direct general excess-charge, bulky-baggage or heavy-bag evidence; researcher-authorised.",
        ])


def update_instructions(workbook) -> None:
    sheet = workbook["Instructions"]
    values = {
        "Final dataset status": STATUS,
        "Researcher authorisation": AUTHORIZATION,
        "Authorisation date": AUTHORIZATION_DATE,
        "Human approval fields": "Left unchanged; researcher authorisation is recorded separately.",
        "Metric exclusion rule": "Cases with unresolved evidence are excluded from affected metrics; behaviour-only cases are excluded from retrieval/context recall.",
    }
    rows = {
        str(sheet.cell(row, 1).value): row
        for row in range(1, sheet.max_row + 1)
        if sheet.cell(row, 1).value
    }
    for label, value in values.items():
        row = rows.get(label)
        if row is None:
            sheet.append([label, value])
        else:
            sheet.cell(row, 2, value)


def main() -> int:
    workbook = load_workbook(WORKBOOK_PATH)
    update_group_sheets(workbook)
    update_leakage_sheet(workbook)
    update_selected_chunks(workbook)
    update_instructions(workbook)
    with NamedTemporaryFile(suffix=".xlsx", dir=WORKBOOK_PATH.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        workbook.save(temporary)
        temporary.replace(WORKBOOK_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Updated {WORKBOOK_PATH.relative_to(PROJECT_DIR)} with status: {STATUS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
