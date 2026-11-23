"""Build a review-only Excel workbook for retrieval evaluation annotations.

The workbook contains proposed answers and candidate evidence. It never marks
an annotation as approved and does not write to Firestore or ChromaDB.
"""

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import chromadb
import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.append(str(BACKEND_DIR))

from app.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import get_embedder


DATASET_PATH = (
    BACKEND_DIR
    / "data"
    / "questions"
    / "retrieval_evaluation_v2_proposed.json"
)
ANNOTATIONS_PATH = (
    BACKEND_DIR
    / "data"
    / "questions"
    / "retrieval_evaluation_v2_annotation_workbook.json"
)
OUTPUT_PATH = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "retrieval-evaluation-human-annotation.xlsx"
)


PROPOSED_ANSWERS = {
    "dev-baggage": "For the specified Africa–Europe Economy routing, the proposed policy answer is 2 checked pieces, each up to 23 kg. The reviewer must confirm route applicability and any operating-carrier exception.",
    "dev-wheelchair": "Request wheelchair assistance at least 48 hours before departure where the stated airport condition applies. Requests inside 48 hours are not guaranteed and remain subject to airport operational constraints.",
    "dev-name": "The proposed international PNR name-correction fee is USD 75 for a correction of up to three characters. A name change is a separate service and fee category.",
    "dev-refund": "A completely unused non-refundable ticket may qualify for a future-travel credit when travel is prevented by Force Majeure, provided the passenger promptly advises Kenya Airways and supplies evidence. An administration fee and validity conditions apply.",
    "dev-upgrade": "Economy-to-Business upgrade options exist, but additional baggage depends on the upgrade product. The evidence indicates that at least one airport-upgrade product does not grant Business Class baggage allowance, so the method must be established before confirming baggage benefits.",
    "dev-checkin": "The proposed answer is that international online check-in opens 48 hours before departure, except flights involving the United States where it opens 24 hours before departure, and remains available until one hour before takeoff.",
    "dev-ambiguous": "Ask for the route and cabin class before giving an allowance because baggage limits vary by itinerary and cabin.",
    "dev-private": "Do not retrieve static policy or expose private booking data. Explain that a current seat assignment requires authenticated booking access or human support.",
    "dev-followup": "Resolve what “that” refers to from a fixed preceding exchange. If the earlier subject cannot be established, ask one focused clarification question.",
    "dev-docs": "Explain that passengers are responsible for holding required travel documents. Do not invent country-specific passport or visa requirements; ask for the itinerary or direct the passenger to authoritative destination requirements when necessary.",
    "held-cabin": "The proposed answer must state the Business Class cabin-baggage allowance for the UK-route column, including the total-weight and personal-accessory conditions shown in the table.",
    "held-minor": "Explain the applicable unaccompanied-minor eligibility, booking/form requirements, fees and route restrictions contained in the Special Care policy. Do not generalise beyond the stated age and operating-flight conditions.",
    "held-rebook": "Self-rebooking is available for a disrupted flight with a schedule change, such as a delayed or cancelled flight. An unaffected flight is not eligible. If the passenger cannot self-rebook, the policy describes automatic rebooking and notification.",
    "held-special": "A passenger may bring an eligible personal wheelchair or mobility aid free of charge and should request assistance at least 48 hours before departure. Late requests may not be guaranteed and are subject to operational constraints.",
    "held-payment": "Kenya Airways supports common cards and listed alternative or mobile payment methods. Availability may depend on market or checkout options, so the response must not imply every method is universally available.",
    "held-delay": "Describe supported disruption options, including eligible self-rebooking and the stated automatic-rebooking process, without claiming that the chatbot completed a booking change.",
    "held-ambiguous": "Ask what the passenger wants to change or resolve the object from the preceding conversation before retrieving a policy.",
    "held-live": "Route the enquiry to the live flight-information workflow and request the flight number/date if required. Do not answer a current gate from static policy evidence.",
    "held-followup": "Use the preceding booking-change context. The proposed policy distinction is that direct or eligible preferred-agent bookings may be modified online, while third-party bookings may require contacting the issuing agent.",
    "held-excess": "Do not infer a general overweight-baggage rule from ordinary allowance tables. Ask for route and bag weight and provide a policy answer only if the reviewer confirms explicit supporting evidence.",
}


SEARCH_TERMS = {
    "dev-baggage": ["Africa", "Europe", "Economy", "23kg"],
    "dev-wheelchair": ["wheelchair", "48 hours", "cannot be guaranteed"],
    "dev-name": ["Name Correction", "International PNRs", "USD 75", "3 characters"],
    "dev-refund": ["completely unused", "Force Majeure", "credit", "administration fee"],
    "dev-upgrade": ["Upgrade to Business Class", "business class baggage allowance", "economy class"],
    "dev-checkin": ["international flights", "48 hours", "United States", "1 hour"],
    "dev-ambiguous": ["ROUTING", "BUSINESS CLASS", "ECONOMY"],
    "dev-docs": ["travel documents", "passport", "visa"],
    "held-cabin": ["Business Class", "EU", "US", "UK", "PERSONAL ACCESSORIES"],
    "held-minor": ["Unaccompanied Minor", "form", "fee", "operated flights"],
    "held-rebook": ["self-rebooking", "delayed", "canceled", "automatically rebook"],
    "held-special": ["own wheelchair", "free of charge", "48 hours"],
    "held-payment": ["payment options", "Visa", "MasterCard", "M-pesa"],
    "held-delay": ["Flight Delays", "Cancellations", "self-rebooking", "automatically rebook"],
    "held-followup": ["travel agent", "Manage My Booking", "3rd party"],
    "held-excess": ["maximum", "32kg", "excess baggage", "weight"],
}


LEAKAGE_REVIEW = {
    "held-cabin": (
        "high",
        "Previously used questions cover Economy and Business baggage allowances. "
        "The cabin-baggage/UK condition is different, but the topic and source overlap strongly.",
    ),
    "held-minor": (
        "low",
        "No earlier evaluation question directly tests unaccompanied-minor policy.",
    ),
    "held-rebook": (
        "medium",
        "Earlier data includes a booking-change question, but not the same disruption-only self-rebooking conditions.",
    ),
    "held-special": (
        "high",
        "Earlier optimisation and diagnostics explicitly used wheelchair ownership, cost and request timing.",
    ),
    "held-payment": (
        "low",
        "Earlier cases mention payment cards or fees, but do not directly evaluate accepted online payment methods.",
    ),
    "held-delay": (
        "medium",
        "A prior mixed question combines cancellation and refund. The new group targets disruption and rebooking, but intent overlap requires review.",
    ),
    "held-ambiguous": (
        "medium",
        "Earlier evaluation includes changing a booking. This case tests ambiguity rather than the policy answer, but may share behavioural expectations.",
    ),
    "held-live": (
        "high",
        "A current-departure-gate question was already used. This is effectively the same unsupported/live-data capability test.",
    ),
    "held-followup": (
        "low",
        "No earlier evaluation question directly tests the travel-agent follow-up distinction.",
    ),
    "held-excess": (
        "medium",
        "Earlier questions test baggage allowances. Overweight handling is a distinct condition but uses the same topic and policy family.",
    ),
}


def tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def active_parent_chunks() -> list[DocumentChunk]:
    settings = get_settings()
    manifest = json.loads(
        (settings.chroma_persist_dir / "active_index.json").read_text(
            encoding="utf-8",
        ),
    )
    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    collection = client.get_collection(
        manifest["collection"],
        embedding_function=None,
    )
    stored = collection.get(include=["metadatas"])
    parents: dict[str, DocumentChunk] = {}
    for metadata in stored["metadatas"]:
        chunk = DocumentChunk.model_validate_json(metadata["payload"])
        parents[chunk.chunk_id] = chunk
    return list(parents.values())


def source_name(chunk: DocumentChunk) -> str:
    return Path(chunk.source.replace("\\", "/")).name


def evidence_excerpt(text: str, terms: list[str]) -> str:
    positions = [
        text.casefold().find(term.casefold())
        for term in terms
        if text.casefold().find(term.casefold()) >= 0
    ]
    if not positions:
        return text[:1400].strip()
    start = max(0, min(positions) - 260)
    end = min(len(text), max(positions) + 1200)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def candidate_evidence(group: dict, chunks: list[DocumentChunk]) -> list[dict]:
    sources = set(group["proposed_sources"])
    terms = SEARCH_TERMS.get(group["semantic_group"], [])
    term_tokens = tokens(" ".join(terms + group["evidence_requirements"]))
    ranked = []
    for chunk in chunks:
        if source_name(chunk) not in sources:
            continue
        chunk_tokens = tokens(chunk.text)
        score = len(term_tokens & chunk_tokens)
        exact = sum(
            term.casefold() in chunk.text.casefold()
            for term in terms
        )
        ranked.append((exact, score, -chunk.chunk_index, chunk))
    ranked.sort(reverse=True, key=lambda item: item[:3])
    selected = []
    covered_sources: set[str] = set()
    for exact, score, _position, chunk in ranked:
        name = source_name(chunk)
        if (exact or score) and len(selected) < 4:
            selected.append({
                "chunk": chunk,
                "excerpt": evidence_excerpt(chunk.text, terms),
            })
            covered_sources.add(name)
    for name in sources - covered_sources:
        fallback = next(
            (item[3] for item in ranked if source_name(item[3]) == name),
            None,
        )
        if fallback is not None and len(selected) < 4:
            selected.append({
                "chunk": fallback,
                "excerpt": evidence_excerpt(fallback.text, terms),
            })
    return selected


def collect_questions(value, origin: str, output: list[dict]) -> None:
    if isinstance(value, dict):
        question = value.get("question")
        if isinstance(question, str) and question.strip():
            output.append({"question": question.strip(), "origin": origin})
        for nested in value.values():
            collect_questions(nested, origin, output)
    elif isinstance(value, list):
        for nested in value:
            collect_questions(nested, origin, output)


def previous_questions() -> list[dict]:
    paths = [
        BACKEND_DIR / "data/questions/retrieval_questions.json",
        BACKEND_DIR / "data/questions/retrieval_diagnostics.json",
        BACKEND_DIR / "data/evaluation/kq_evaluation_v1.json",
        BACKEND_DIR / "data/evaluation/model-results.json",
        BACKEND_DIR / "data/evaluation/retrieval-results.json",
        BACKEND_DIR / "data/evaluation/corrected-context-results.json",
        BACKEND_DIR / "data/evaluation/optimized-context-results.json",
        BACKEND_DIR / "data/evaluation/context-diagnostics.json",
        BACKEND_DIR / "data/evaluation/hybrid-retrieval-comparison.json",
    ]
    found: list[dict] = []
    for path in paths:
        if path.exists():
            collect_questions(
                json.loads(path.read_text(encoding="utf-8")),
                path.name,
                found,
            )
    unique = {}
    for item in found:
        unique[(item["question"], item["origin"])] = item
    return list(unique.values())


def overlap_rows(held_out: list[dict], historical: list[dict]) -> list[dict]:
    embedder = get_embedder()
    historical_text = [item["question"] for item in historical]
    held_text = [item["question"] for item in held_out]
    historical_vectors = np.asarray([
        embedder.embed_query(text)
        for text in historical_text
    ])
    held_vectors = np.asarray([
        embedder.embed_query(text)
        for text in held_text
    ])
    similarities = held_vectors @ historical_vectors.T
    rows = []
    for index, case in enumerate(held_out):
        best = np.argsort(-similarities[index])[:3]
        for rank, historical_index in enumerate(best, 1):
            score = float(similarities[index, historical_index])
            level = "high" if score >= 0.90 else "medium" if score >= 0.82 else "low"
            rows.append({
                "case_id": case["id"],
                "semantic_group": case["semantic_group"],
                "language": case["language"],
                "held_out_question": case["question"],
                "match_rank": rank,
                "similarity": score,
                "overlap_level": level,
                "previous_question": historical[historical_index]["question"],
                "previous_origin": historical[historical_index]["origin"],
                "reviewer_leakage_decision": "",
                "reviewer_comments": "",
            })
    return rows


def style_sheet(sheet, widths: dict[int, float]) -> None:
    navy = "0B2E63"
    light_blue = "DCEAF7"
    thin = Side(style="thin", color="CAD6E2")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 34
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = Border(bottom=thin)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = Border(bottom=thin)
        if row[0].row % 2 == 0:
            for cell in row:
                cell.fill = PatternFill("solid", fgColor="F7FAFD")
    for column, width in widths.items():
        sheet.column_dimensions[chr(64 + column)].width = width
    sheet.sheet_view.showGridLines = False


def add_review_sheet(workbook, name: str, groups: list[dict], cases_by_group, evidence_by_group) -> None:
    sheet = workbook.create_sheet(name)
    headers = [
        "Semantic group", "English question", "Kiswahili question",
        "Code-switched question", "Expected chatbot behaviour",
        "Proposed reference answer — NOT VALIDATED", "Source documents",
        "Actual candidate evidence from indexed parents — NOT VALIDATED",
        "Parent chunk identifiers", "Source URLs",
        "Policy conditions and exceptions", "Uncertainties for review",
        "Reviewer approval", "Reviewer comments",
    ]
    sheet.append(headers)
    approval = DataValidation(
        type="list",
        formula1='"Approved,Rejected,Needs changes"',
        allow_blank=True,
    )
    sheet.add_data_validation(approval)
    for group in groups:
        variants = {
            case["language"]: case["question"]
            for case in cases_by_group[group["semantic_group"]]
        }
        evidence = evidence_by_group[group["semantic_group"]]
        excerpts = "\n\n".join(
            f"[{source_name(item['chunk'])} | chunk {item['chunk'].chunk_id}]\n{item['excerpt']}"
            for item in evidence
        )
        identifiers = "\n".join(
            item["chunk"].chunk_id
            for item in evidence
        )
        urls = "\n".join(dict.fromkeys(
            item["chunk"].source_url or "[No source URL in indexed metadata]"
            for item in evidence
        ))
        conditions = "\n• ".join(["", *group["evidence_requirements"]]).strip()
        sheet.append([
            group["semantic_group"],
            variants.get("en", ""),
            variants.get("sw", ""),
            variants.get("mixed", ""),
            group["expected_behaviour"],
            PROPOSED_ANSWERS[group["semantic_group"]],
            "\n".join(group["proposed_sources"]),
            excerpts or "No static policy evidence proposed.",
            identifiers,
            urls,
            conditions,
            group["document_review_note"],
            "",
            "",
        ])
        approval.add(sheet.cell(sheet.max_row, 13))
        sheet.row_dimensions[sheet.max_row].height = 180
    style_sheet(sheet, {
        1: 20, 2: 34, 3: 34, 4: 34, 5: 25, 6: 52, 7: 28,
        8: 78, 9: 46, 10: 45, 11: 45, 12: 45, 13: 18, 14: 42,
    })
    sheet.conditional_formatting.add(
        f"M2:M{sheet.max_row}",
        FormulaRule(formula=["M2=\"Approved\""], fill=PatternFill("solid", fgColor="D9EAD3")),
    )


def build_workbook() -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    annotation = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    chunks = active_parent_chunks()
    cases_by_group = defaultdict(list)
    for case in dataset["cases"]:
        cases_by_group[case["semantic_group"]].append(case)
    evidence_by_group = {
        group["semantic_group"]: candidate_evidence(group, chunks)
        for group in annotation["groups"]
    }

    workbook = Workbook()
    instructions = workbook.active
    instructions.title = "Instructions"
    instructions.append(["Kenya Airways RAG Evaluation — Human Annotation Workbook"])
    instructions.append(["Generated", datetime.now(timezone.utc).isoformat()])
    instructions.append(["Status", "All answers, evidence and chunks are proposed and NOT VALIDATED."])
    instructions.append(["Approval", "Only a human reviewer may select Approved, Rejected or Needs changes."])
    instructions.append(["Development", "May be used for tuning only after human approval."])
    instructions.append(["Held-out", "Must not be used for retrieval or prompt tuning."])
    instructions.append(["Evidence", "Check excerpts against the original DOCX in backend/data/raw/KQ Data."])
    instructions.append(["Chunk IDs", "Confirm each indexed parent chunk supports the reference answer and conditions."])
    instructions.append(["Overlap audit", "Review medium/high similarities and move or replace leaked held-out groups before evaluation."])
    instructions.column_dimensions["A"].width = 24
    instructions.column_dimensions["B"].width = 110
    for row in instructions.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    instructions["A1"].font = Font(bold=True, color="FFFFFF", size=15)
    instructions["A1"].fill = PatternFill("solid", fgColor="0B2E63")
    instructions.merge_cells("A1:B1")
    instructions.sheet_view.showGridLines = False

    development = [
        group for group in annotation["groups"]
        if group["partition"] == "development"
    ]
    held_out = [
        group for group in annotation["groups"]
        if group["partition"] == "held_out"
    ]
    add_review_sheet(
        workbook, "Development", development,
        cases_by_group, evidence_by_group,
    )
    add_review_sheet(
        workbook, "Held-out", held_out,
        cases_by_group, evidence_by_group,
    )

    leakage = workbook.create_sheet("Leakage summary")
    leakage.append([
        "Held-out semantic group", "Review priority",
        "Reason for potential overlap", "Reviewer partition decision",
        "Replacement group/question if required", "Reviewer comments",
    ])
    decision = DataValidation(
        type="list",
        formula1='"Keep held-out,Move to development,Replace group"',
        allow_blank=True,
    )
    leakage.add_data_validation(decision)
    for group in held_out:
        priority, reason = LEAKAGE_REVIEW[group["semantic_group"]]
        leakage.append([
            group["semantic_group"], priority, reason, "", "", "",
        ])
        decision.add(leakage.cell(leakage.max_row, 4))
    style_sheet(leakage, {
        1: 24, 2: 16, 3: 85, 4: 28, 5: 50, 6: 45,
    })
    leakage.conditional_formatting.add(
        f"B2:B{leakage.max_row}",
        FormulaRule(formula=["B2=\"high\""], fill=PatternFill("solid", fgColor="F4CCCC")),
    )
    leakage.conditional_formatting.add(
        f"B2:B{leakage.max_row}",
        FormulaRule(formula=["B2=\"medium\""], fill=PatternFill("solid", fgColor="FFF2CC")),
    )

    overlap = workbook.create_sheet("Held-out overlap audit")
    overlap_headers = [
        "Case ID", "Semantic group", "Language", "Held-out question",
        "Match rank", "E5 cosine similarity", "Overlap level",
        "Previously used question", "Previous source file",
        "Reviewer leakage decision", "Reviewer comments",
    ]
    overlap.append(overlap_headers)
    held_cases = [
        case for case in dataset["cases"]
        if case["partition"] == "held_out"
    ]
    for row in overlap_rows(held_cases, previous_questions()):
        overlap.append(list(row.values()))
    style_sheet(overlap, {
        1: 24, 2: 20, 3: 12, 4: 50, 5: 12, 6: 18,
        7: 14, 8: 50, 9: 30, 10: 25, 11: 40,
    })
    overlap.conditional_formatting.add(
        f"G2:G{overlap.max_row}",
        FormulaRule(formula=["G2=\"high\""], fill=PatternFill("solid", fgColor="F4CCCC")),
    )
    overlap.conditional_formatting.add(
        f"G2:G{overlap.max_row}",
        FormulaRule(formula=["G2=\"medium\""], fill=PatternFill("solid", fgColor="FFF2CC")),
    )

    source_sheet = workbook.create_sheet("Selected chunk index")
    source_sheet.append([
        "Semantic group", "Partition", "Document", "Parent chunk ID",
        "Chunk index", "Section", "Source URL", "Candidate excerpt",
        "Human evidence decision", "Reviewer comments",
    ])
    group_by_id = {
        group["semantic_group"]: group
        for group in annotation["groups"]
    }
    for identifier, evidence in evidence_by_group.items():
        group = group_by_id[identifier]
        for item in evidence:
            chunk = item["chunk"]
            source_sheet.append([
                identifier, group["partition"], source_name(chunk),
                chunk.chunk_id, chunk.chunk_index, chunk.section or "",
                chunk.source_url or "", item["excerpt"], "", "",
            ])
    style_sheet(source_sheet, {
        1: 20, 2: 14, 3: 34, 4: 68, 5: 12,
        6: 30, 7: 58, 8: 90, 9: 24, 10: 40,
    })
    for row in range(2, source_sheet.max_row + 1):
        source_sheet.row_dimensions[row].height = 120

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUTPUT_PATH)

    # Reopen the file so a corrupt package fails during generation rather than
    # being discovered by the reviewer later.
    checked = load_workbook(OUTPUT_PATH, read_only=False, data_only=False)
    required = {
        "Instructions", "Development", "Held-out",
        "Leakage summary", "Held-out overlap audit",
        "Selected chunk index",
    }
    if set(checked.sheetnames) != required:
        raise ValueError("Generated workbook sheets are incomplete.")
    if checked["Development"].max_row != 11:
        raise ValueError("Development sheet must contain ten groups.")
    if checked["Held-out"].max_row != 11:
        raise ValueError("Held-out sheet must contain ten groups.")
    checked.close()
    print(f"Created and validated {OUTPUT_PATH}")


if __name__ == "__main__":
    build_workbook()
