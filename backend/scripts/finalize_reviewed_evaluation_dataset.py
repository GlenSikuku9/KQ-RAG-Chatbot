"""Validate the reviewed evaluation workbook and export private JSON datasets.

This script is intentionally independent from retrieval. It reads the workbook,
the original DOCX files, and parent payloads stored in the active Chroma index.
It never queries the index, embeds questions, changes production data, or assigns
human approval.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any

import chromadb
from openpyxl import load_workbook


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.append(str(BACKEND_DIR))

from app.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.rag.document_loader import load_knowledge_base_document
from scripts.evaluation_annotation_corrections import corrected_parent_ids


WORKBOOK_PATH = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "retrieval-evaluation-AI-reviewed.xlsx"
)
RAW_DOCUMENT_DIR = BACKEND_DIR / "data" / "raw" / "KQ Data"
DEVELOPMENT_OUTPUT = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "retrieval-evaluation-development-reviewed.json"
)
HELD_OUT_OUTPUT = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "retrieval-evaluation-held-out-reviewed.json"
)
REPORT_OUTPUT = (
    PROJECT_DIR
    / "docs"
    / "reviewed-evaluation-dataset-validation.md"
)
HISTORICAL_QUESTION_PATHS = (
    BACKEND_DIR / "data/questions/retrieval_questions.json",
    BACKEND_DIR / "data/questions/retrieval_diagnostics.json",
    BACKEND_DIR / "data/evaluation/kq_evaluation_v1.json",
    BACKEND_DIR / "data/evaluation/model-results.json",
    BACKEND_DIR / "data/evaluation/retrieval-results.json",
    BACKEND_DIR / "data/evaluation/corrected-context-results.json",
    BACKEND_DIR / "data/evaluation/optimized-context-results.json",
    BACKEND_DIR / "data/evaluation/context-diagnostics.json",
    BACKEND_DIR / "data/evaluation/hybrid-retrieval-comparison.json",
)

EXPECTED_SHEETS = (
    "Instructions",
    "Development",
    "Held-out",
    "Leakage summary",
    "Held-out overlap audit",
    "Selected chunk index",
    "Review log",
    "Coverage",
)
LANGUAGE_COLUMNS = {
    "en": ("English question", "Fixed history — English"),
    "sw": ("Kiswahili question", "Fixed history — Kiswahili"),
    "mixed": ("Code-switched question", "Fixed history — Code-switched"),
}

# These are policy-text locators, not retrieval expansions. They are used only
# to verify evidence labels in this offline annotation artefact.
EVIDENCE_ANCHORS = {
    "dev-baggage": ("Africa <-> America/Europe", "2 PC 23kg/50lbs max each", "e-ticket"),
    "dev-wheelchair": ("Wheelchair services shall be provided", "LHR", "48 hours"),
    "dev-name": ("International PNRs", "USD 75", "maximum of 3 characters", "name correction"),
    "dev-refund": ("completely unused", "Force Majeure", "administration fee", "twelve (12) months"),
    "dev-upgrade": ("baggage allowance will remain", "original Economy class ticket", "Upgrade at the Airport"),
    "dev-checkin": ("two and a half hours", "Guangzhou", "online check-in window opens 48 hours", "check-in deadlines"),
    "dev-ambiguous": ("Free Checked Baggage Allowance", "Hand Baggage"),
    "dev-followup": ("online check-in window opens 48 hours", "24 hours before for flights involving the United States"),
    "dev-docs": ("travel documents and visas", "exit, entry, health", "name on the Ticket must exactly match"),
    "dev-delayed": ("file reference", "3274747", "Present yourself at the desk"),
    "dev-card-refund": ("21-30 business days", "bank processing"),
    "dev-contact": ("Global WhatsApp", "Open 24 hours", "+254 705 474 747"),
    "held-special": ("Wheelchair services shall be provided", "48 hours", "mobility aid"),
    "held-cabin": ("18kg", "PERSONAL ACCESSORIES", "L55xW25xH35cm"),
    "held-minor": ("Unaccompanied Minor", "5 years", "overnight connections"),
    "held-rebook": ("Only flights with a schedule change", "automatically rebook", "free"),
    "held-payment": ("M-pesa", "Airtel Money", "PayPal", "real-time bank transfer"),
    "held-followup": ("preferred travel agents", "Manage My Booking", "3rd party"),
    "held-excess": ("32kgs", "300cm", "Heavy Bag Charges", "charge for carriage of Baggage in excess"),
    "held-pet": ("will not be accepted as checked-in Animal in Hold", "PETC", "cargo", "individually trained"),
    "held-needles": ("medically necessary needles and syringes", "professionally printed label", "Medication on Board"),
    "held-pooling": ("pooling the number of baggage but not the weight",),
    "held-kosher": ("KSML", "48 hours", "may not be available"),
    "held-pregnancy": ("Multiple Uncomplicated pregnancies", "32 weeks", "28 weeks"),
    "held-noshow": ("do not show up", "cancel your return", "onward reservations"),
    "held-valuables": ("jewellery", "passports", "identification documents"),
    "held-action": ("Book a Flight", "book your flight"),
}

# These behaviours intentionally do not require static policy evidence.
NO_STATIC_EVIDENCE_GROUPS = {
    "dev-private",
    "dev-ambiguous",
    "held-live",
    "held-ambiguous",
    "held-action",
}
EVIDENCE_CHUNK_ALLOWLIST = {
    "held-excess": {
        ("Baggage Conditions Rules.docx", 0),
        ("Baggage Allowance Information.docx", 11),
        ("Baggage Allowance Information.docx", 12),
        ("Payments.docx", 18),
    },
}
DOCUMENTED_POLICY_CONFLICTS = {
    "dev-checkin": (
        "The Check-in document says complete international check-in 2.5 hours before departure, "
        "while Conditions of Carriage article 6.1 states airport-varying minimums including "
        "120 minutes for long-haul international flights. The dataset preserves both and does "
        "not collapse them into one universal deadline."
    ),
    "held-pet": (
        "General carriage clauses describe animals conditionally if accepted; the specific Pet "
        "Carriage provision controls ordinary Kenya Airways pet carriage and says cargo only."
    ),
    "held-kosher": (
        "The general special-meal instruction says 24 hours; the specific kosher-meal provision "
        "requires 48 hours and controls the kosher request."
    ),
}


def normalize_text(value: str | None) -> str:
    """Normalize whitespace and punctuation for duplicate/content comparisons."""

    if not value:
        return ""
    value = value.casefold().replace("–", "-").replace("—", "-")
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def split_lines(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def collect_questions(value: Any, origin: str, output: list[dict[str, str]]) -> None:
    if isinstance(value, dict):
        question = value.get("question")
        if isinstance(question, str) and question.strip():
            output.append({"question": question.strip(), "origin": origin})
        for nested in value.values():
            collect_questions(nested, origin, output)
    elif isinstance(value, list):
        for nested in value:
            collect_questions(nested, origin, output)


def historical_questions() -> tuple[list[dict[str, str]], list[str]]:
    found: list[dict[str, str]] = []
    scanned = []
    for path in HISTORICAL_QUESTION_PATHS:
        if not path.exists():
            continue
        collect_questions(
            json.loads(path.read_text(encoding="utf-8")),
            str(path.relative_to(PROJECT_DIR)),
            found,
        )
        scanned.append(str(path.relative_to(PROJECT_DIR)))
    unique = {
        (normalize_text(item["question"]), item["origin"]): item
        for item in found
        if normalize_text(item["question"])
    }
    return list(unique.values()), scanned


def token_overlap(left: str, right: str) -> float:
    left_tokens = set(normalize_text(left).split())
    right_tokens = set(normalize_text(right).split())
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def historical_leakage_audit(cases: list[dict[str, Any]]) -> dict[str, Any]:
    historical, scanned = historical_questions()
    results = []
    for case in cases:
        ranked = sorted(
            (
                {
                    "question": item["question"],
                    "origin": item["origin"],
                    "exact_normalized_match": (
                        normalize_text(case["question"])
                        == normalize_text(item["question"])
                    ),
                    "token_jaccard": token_overlap(case["question"], item["question"]),
                }
                for item in historical
            ),
            key=lambda item: (-item["exact_normalized_match"], -item["token_jaccard"], item["origin"]),
        )[:3]
        results.append({"case_id": case["case_id"], "top_historical_matches": ranked})
    exact = [
        result["case_id"]
        for result in results
        if result["top_historical_matches"]
        and result["top_historical_matches"][0]["exact_normalized_match"]
    ]
    return {
        "method": "normalized exact match and token-set Jaccard; no model or embedding call",
        "historical_files_scanned": scanned,
        "historical_question_records": len(historical),
        "current_held_out_cases_checked": len(cases),
        "exact_historical_matches": exact,
        "results": results,
    }


def rows_by_header(sheet) -> list[dict[str, Any]]:
    values = list(sheet.values)
    headers = [str(value) if value is not None else "" for value in values[0]]
    return [dict(zip(headers, row, strict=True)) for row in values[1:]]


def active_parent_chunks() -> tuple[list[DocumentChunk], dict[str, Any]]:
    settings = get_settings()
    manifest_path = settings.chroma_persist_dir / "active_index.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    collection = client.get_collection(
        manifest["collection"],
        embedding_function=None,
    )
    stored = collection.get(include=["metadatas"])
    parents: dict[str, DocumentChunk] = {}
    for metadata in stored["metadatas"]:
        parent = DocumentChunk.model_validate_json(metadata["payload"])
        previous = parents.get(parent.chunk_id)
        if previous is not None and previous != parent:
            raise ValueError(f"Conflicting indexed payload for {parent.chunk_id}.")
        parents[parent.chunk_id] = parent
    return list(parents.values()), manifest


def source_name(chunk: DocumentChunk) -> str:
    return Path(chunk.source.replace("\\", "/")).name


def verify_document_versions(
    chunks: list[DocumentChunk],
) -> dict[str, dict[str, Any]]:
    """Confirm active parents match content extracted from the local DOCX files."""

    versions_by_source: dict[str, set[str]] = defaultdict(set)
    for chunk in chunks:
        versions_by_source[source_name(chunk)].add(chunk.version)

    results: dict[str, dict[str, Any]] = {}
    for document, versions in sorted(versions_by_source.items()):
        path = RAW_DOCUMENT_DIR / document
        file_digest = sha256(path.read_bytes()).hexdigest() if path.exists() else None
        extracted_version = (
            load_knowledge_base_document(path, RAW_DOCUMENT_DIR).version
            if path.exists()
            else None
        )
        results[document] = {
            "path": str(path.relative_to(PROJECT_DIR)) if path.exists() else None,
            "file_sha256": file_digest,
            "extracted_content_version": extracted_version,
            "indexed_versions": sorted(versions),
            "matches_active_index": bool(
                extracted_version and versions == {extracted_version}
            ),
        }
    return results


def candidate_ids_from_workbook(
    workbook,
) -> dict[str, set[str]]:
    """Read reviewed evidence decisions without using retrieval ranks."""

    candidates: dict[str, set[str]] = defaultdict(set)
    for row in rows_by_header(workbook["Selected chunk index"]):
        if row.get("AI evidence review") != "Candidate supports required evidence":
            continue
        identifier = row.get("Parent chunk ID")
        if identifier:
            candidates[str(row["Semantic group"])].add(str(identifier))
    return candidates


def map_evidence(
    group: dict[str, Any],
    parents: list[DocumentChunk],
    reviewed_ids: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map source-backed evidence using content anchors and reviewed candidates."""

    group_id = str(group["Semantic group"])
    sources = set(split_lines(group.get("Source documents")))
    explicit_ids = set(split_lines(group.get("Candidate parent IDs — manifest confirmation pending")))
    explicit_ids.update(reviewed_ids.get(group_id, set()))
    anchors = EVIDENCE_ANCHORS.get(group_id, ())
    selected: dict[str, tuple[DocumentChunk, list[str], set[str]]] = {}
    issues: list[str] = []

    for chunk in parents:
        document = source_name(chunk)
        if document not in sources:
            continue
        allowlist = EVIDENCE_CHUNK_ALLOWLIST.get(group_id)
        if allowlist is not None and (document, chunk.chunk_index) not in allowlist:
            continue
        matched = [
            anchor
            for anchor in anchors
            if normalize_text(anchor) in normalize_text(chunk.text)
        ]
        reasons: set[str] = set()
        if chunk.chunk_id in explicit_ids:
            reasons.add("workbook_reviewed_candidate")
        if matched:
            reasons.add("policy_text_anchor_match")
        if reasons:
            selected[chunk.chunk_id] = (chunk, matched, reasons)

    active_ids = {chunk.chunk_id for chunk in parents}
    for identifier in sorted(explicit_ids - active_ids):
        issues.append(f"Workbook parent ID is not active: {identifier}")

    for identifier in sorted(explicit_ids & active_ids):
        chunk = next(chunk for chunk in parents if chunk.chunk_id == identifier)
        if source_name(chunk) not in sources:
            issues.append(
                f"Workbook parent ID {identifier} belongs to unexpected source "
                f"{source_name(chunk)}."
            )

    evidence = []
    for identifier, (chunk, matched, reasons) in sorted(
        selected.items(),
        key=lambda item: (source_name(item[1][0]), item[1][0].chunk_index),
    ):
        evidence.append({
            "document": source_name(chunk),
            "parent_chunk_id": identifier,
            "chunk_index": chunk.chunk_index,
            "section": chunk.section,
            "source_url": chunk.source_url,
            "source_locations": chunk.source_locations,
            "text": chunk.text,
            "matched_policy_anchors": matched,
            "mapping_reasons": sorted(reasons),
        })

    if sources and not evidence:
        issues.append("No active parent chunk was mapped for the declared source documents.")
    if group_id not in NO_STATIC_EVIDENCE_GROUPS and sources and not anchors:
        issues.append("No independent policy-text anchors are defined for this annotation.")
    return evidence, issues


def evidence_criteria(
    group_id: str,
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Express required policy locators as any-of sets of equivalent parents."""

    if group_id in NO_STATIC_EVIDENCE_GROUPS:
        return [], []
    criteria = []
    issues = []
    for index, anchor in enumerate(EVIDENCE_ANCHORS.get(group_id, ()), 1):
        acceptable = [
            item["parent_chunk_id"]
            for item in evidence
            if anchor in item["matched_policy_anchors"]
        ]
        criterion_id = f"{group_id}-evidence-{index}"
        acceptable, _removed = corrected_parent_ids(
            criterion_id,
            acceptable,
        )
        status = "mapped" if acceptable else "unresolved_no_active_parent_mapping"
        if not acceptable:
            issues.append(f"Required evidence locator has no active parent mapping: {anchor}")
        criteria.append({
            "criterion_id": criterion_id,
            "policy_text_locator": anchor,
            "requirement": "mandatory",
            "matching_rule": "any_of_equivalent_parent_chunks",
            "acceptable_parent_chunk_ids": acceptable,
            "status": status,
        })
    return criteria, issues


def leakage_for_group(workbook, group_id: str) -> dict[str, Any] | None:
    for row in rows_by_header(workbook["Leakage summary"]):
        if row.get("Held-out semantic group") == group_id:
            return {
                "review_priority": row.get("Review priority"),
                "reason": row.get("Reason for potential overlap"),
                "human_partition_decision": row.get("Reviewer partition decision"),
                "human_comments": row.get("Reviewer comments"),
                "ai_recommendation": row.get("AI partition recommendation (not human approval)"),
                "ai_rationale": row.get("AI rationale"),
            }
    return None


def build_partition(
    workbook,
    sheet_name: str,
    partition: str,
    parents: list[DocumentChunk],
    reviewed_ids: dict[str, set[str]],
    document_versions: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    groups = []
    cases = []
    for row in rows_by_header(workbook[sheet_name]):
        group_id = str(row["Semantic group"])
        evidence, mapping_issues = map_evidence(row, parents, reviewed_ids)
        criteria, criteria_issues = evidence_criteria(group_id, evidence)
        review_notes = [str(row["AI reviewer comments / uncertainties"])] if row.get(
            "AI reviewer comments / uncertainties"
        ) else []
        uncertainties = []
        uncertainties.extend(mapping_issues)
        uncertainties.extend(criteria_issues)
        leakage = leakage_for_group(workbook, group_id) if partition == "held_out" else None
        if leakage and not leakage["human_partition_decision"]:
            leakage["researcher_authorised_decision"] = "Retain"
            leakage["researcher_authorised_rationale"] = (
                "Distinct information need and supporting parent evidence; historical topic "
                "similarity is documented but does not duplicate the tested policy claim."
            )

        questions = []
        for language, (question_column, history_column) in LANGUAGE_COLUMNS.items():
            question = str(row[question_column]).strip()
            case = {
                "case_id": f"{group_id}-{language}",
                "semantic_group": group_id,
                "partition": partition,
                "language": language,
                "question": question,
                "fixed_history": row.get(history_column),
                "expected_behavior": row["Expected chatbot behaviour"],
                "reference_answer": row["AI-reviewed reference answer — human approval pending"],
                "candidate_parent_chunk_ids_not_for_recall_scoring": [
                    item["parent_chunk_id"] for item in evidence
                ],
                "mandatory_evidence_criteria": criteria,
                "evidence_scoring": {
                    "unit": "mandatory_evidence_criterion",
                    "criterion_match": "retrieve_at_least_one_acceptable_parent_chunk",
                    "do_not_score_flat_candidate_id_recall": True,
                },
                "dataset_status": "AI-assisted validated and researcher-authorised",
                "metric_eligibility": {
                    "retrieval_and_context_recall": (
                        "excluded_behavior_only"
                        if group_id in NO_STATIC_EVIDENCE_GROUPS
                        else "eligible"
                    ),
                    "answer_correctness": (
                        "exclude_single_value_scoring_policy_conflict"
                        if group_id == "dev-checkin"
                        else "eligible"
                    ),
                },
                "human_approval": None,
            }
            questions.append({
                "case_id": case["case_id"],
                "language": language,
                "question": question,
                "fixed_history": row.get(history_column),
            })
            cases.append(case)

        status = (
            "unresolved_human_review_required"
            if uncertainties
            else "verified_against_snapshot_human_approval_pending"
        )
        groups.append({
            "semantic_group": group_id,
            "partition": partition,
            "questions": questions,
            "expected_behavior": row["Expected chatbot behaviour"],
            "reference_answer": row["AI-reviewed reference answer — human approval pending"],
            "source_documents": split_lines(row.get("Source documents")),
            "evidence_requirement": row.get("Reviewed source evidence / section locator"),
            "evidence": evidence,
            "mandatory_evidence_criteria": criteria,
            "supplementary_reviewed_parent_chunk_ids": [
                item["parent_chunk_id"]
                for item in evidence
                if "workbook_reviewed_candidate" in item["mapping_reasons"]
                and not item["matched_policy_anchors"]
            ],
            "policy_conditions_and_exceptions": row.get("Policy conditions and exceptions"),
            "verification_status": (
                "ai_assisted_validated_researcher_authorised"
                if not uncertainties
                else status
            ),
            "dataset_status": "AI-assisted validated and researcher-authorised",
            "researcher_authorisation": True,
            "ai_review_notes": review_notes,
            "unresolved_items": uncertainties,
            "documented_policy_conflict": DOCUMENTED_POLICY_CONFLICTS.get(group_id),
            "metric_exclusions": (
                ["single_value_answer_correctness"] if group_id == "dev-checkin" else []
            ),
            "leakage_review": leakage,
            "human_review": {
                "approval": row.get("Human reviewer approval"),
                "comments": row.get("Human reviewer comments"),
            },
        })

    return {
        "schema_version": "kq-retrieval-evaluation-reviewed-v2",
        "partition": partition,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_workbook": str(WORKBOOK_PATH.relative_to(PROJECT_DIR)),
        "source_workbook_sha256": sha256(WORKBOOK_PATH.read_bytes()).hexdigest(),
        "active_index": {
            "collection": manifest["collection"],
            "content_hash": manifest["content_hash"],
            "search_segment_count": manifest["count"],
            "active_parent_chunk_count": len(parents),
        },
        "document_version_validation": document_versions,
        "dataset_status": "AI-assisted validated and researcher-authorised",
        "researcher_authorisation": True,
        "human_annotation_approval": None,
        "semantic_groups": groups,
        "cases": cases,
    }


def validate_datasets(development: dict, held_out: dict) -> dict[str, Any]:
    errors = []
    all_cases = development["cases"] + held_out["cases"]
    all_groups = development["semantic_groups"] + held_out["semantic_groups"]
    case_ids = [case["case_id"] for case in all_cases]
    normalized_questions = defaultdict(list)
    for case in all_cases:
        normalized_questions[normalize_text(case["question"])].append(case["case_id"])

    if len(development["semantic_groups"]) != 15:
        errors.append("Development must contain 15 semantic groups.")
    if len(held_out["semantic_groups"]) != 15:
        errors.append("Held-out must contain 15 semantic groups.")
    if len(development["cases"]) != 45:
        errors.append("Development must contain 45 cases.")
    if len(held_out["cases"]) != 45:
        errors.append("Held-out must contain 45 cases.")
    if len(case_ids) != len(set(case_ids)):
        errors.append("Case IDs are not unique.")

    required_group_fields = (
        "semantic_group",
        "partition",
        "expected_behavior",
        "reference_answer",
        "evidence_requirement",
        "policy_conditions_and_exceptions",
        "verification_status",
    )
    expected_languages = set(LANGUAGE_COLUMNS)
    for group in all_groups:
        group_id = group["semantic_group"]
        missing = [field for field in required_group_fields if not group.get(field)]
        if missing:
            errors.append(f"{group_id} is missing required fields: {missing}")
        languages = {question["language"] for question in group["questions"]}
        if len(group["questions"]) != 3 or languages != expected_languages:
            errors.append(f"{group_id} must contain exactly one question per language.")
        if group["source_documents"] and not group["evidence"]:
            errors.append(f"{group_id} declares sources but has no mapped evidence.")
        evidence_ids = [item["parent_chunk_id"] for item in group["evidence"]]
        if len(evidence_ids) != len(set(evidence_ids)):
            errors.append(f"{group_id} contains duplicate evidence IDs.")
        for criterion in group["mandatory_evidence_criteria"]:
            unknown = sorted(
                set(criterion["acceptable_parent_chunk_ids"]) - set(evidence_ids)
            )
            if unknown:
                errors.append(
                    f"{criterion['criterion_id']} references unmapped evidence: {unknown}"
                )
            if criterion["matching_rule"] != "any_of_equivalent_parent_chunks":
                errors.append(f"{criterion['criterion_id']} has an unsafe matching rule.")
        unexpected_sources = sorted(
            {item["document"] for item in group["evidence"]}
            - set(group["source_documents"])
        )
        if unexpected_sources:
            errors.append(
                f"{group_id} maps evidence from undeclared sources: {unexpected_sources}"
            )

    for dataset in (development, held_out):
        groups_by_id = {
            group["semantic_group"]: group for group in dataset["semantic_groups"]
        }
        for case in dataset["cases"]:
            group = groups_by_id.get(case["semantic_group"])
            if group is None:
                errors.append(f"{case['case_id']} has no semantic-group record.")
                continue
            if case["partition"] != dataset["partition"]:
                errors.append(f"{case['case_id']} has an incorrect partition label.")
            if case["reference_answer"] != group["reference_answer"]:
                errors.append(f"{case['case_id']} reference answer differs from its group.")
            if case["candidate_parent_chunk_ids_not_for_recall_scoring"] != [
                item["parent_chunk_id"] for item in group["evidence"]
            ]:
                errors.append(f"{case['case_id']} evidence IDs differ from its group.")
            if not case["evidence_scoring"]["do_not_score_flat_candidate_id_recall"]:
                errors.append(f"{case['case_id']} permits unsafe flat evidence scoring.")
            if case["mandatory_evidence_criteria"] != group["mandatory_evidence_criteria"]:
                errors.append(f"{case['case_id']} evidence criteria differ from its group.")

    for name, versions in development["document_version_validation"].items():
        if not versions["matches_active_index"]:
            errors.append(f"Indexed content version differs from local DOCX: {name}")

    development_groups = {
        group["semantic_group"] for group in development["semantic_groups"]
    }
    held_out_groups = {
        group["semantic_group"] for group in held_out["semantic_groups"]
    }
    overlap = sorted(development_groups & held_out_groups)
    if overlap:
        errors.append(f"Semantic groups cross partitions: {overlap}")

    exact_duplicates = {
        question: identifiers
        for question, identifiers in normalized_questions.items()
        if question and len(identifiers) > 1
    }
    if exact_duplicates:
        errors.append(f"Exact normalized duplicate questions: {exact_duplicates}")

    active_ids = {
        item["parent_chunk_id"]
        for dataset in (development, held_out)
        for group in dataset["semantic_groups"]
        for item in group["evidence"]
    }
    missing_evidence_text = [
        item["parent_chunk_id"]
        for dataset in (development, held_out)
        for group in dataset["semantic_groups"]
        for item in group["evidence"]
        if not item["text"].strip()
    ]
    if missing_evidence_text:
        errors.append(f"Mapped chunks without text: {missing_evidence_text}")

    return {
        "valid": not errors,
        "errors": errors,
        "total_cases": len(all_cases),
        "total_semantic_groups": len(development_groups | held_out_groups),
        "languages": dict(Counter(case["language"] for case in all_cases)),
        "partition_case_counts": {
            "development": len(development["cases"]),
            "held_out": len(held_out["cases"]),
        },
        "mapped_unique_parent_chunks": len(active_ids),
        "groups_with_mapped_evidence": sum(bool(group["evidence"]) for group in all_groups),
        "behavior_only_groups": sum(
            group["semantic_group"] in NO_STATIC_EVIDENCE_GROUPS
            for group in all_groups
        ),
        "groups_requiring_human_review": sum(bool(group["unresolved_items"]) for group in all_groups),
        "held_out_human_partition_decisions_blank": sum(
            bool(group["leakage_review"])
            and not group["leakage_review"]["human_partition_decision"]
            for group in held_out["semantic_groups"]
        ),
        "verified_source_documents": len(development["document_version_validation"]),
        "exact_duplicate_questions": exact_duplicates,
        "semantic_group_overlap": overlap,
    }


def write_report(
    development: dict,
    held_out: dict,
    validation: dict[str, Any],
) -> None:
    unresolved = [
        group
        for dataset in (development, held_out)
        for group in dataset["semantic_groups"]
        if group["unresolved_items"]
    ]
    mapped = [
        group
        for dataset in (development, held_out)
        for group in dataset["semantic_groups"]
        if group["evidence"]
    ]
    lines = [
        "# Reviewed Evaluation Dataset Validation",
        "",
        "## Outcome",
        "",
        f"- Structural validation: **{'passed' if validation['valid'] else 'failed'}**.",
        f"- Questions: **{validation['total_cases']}**.",
        f"- Semantic groups: **{validation['total_semantic_groups']}**.",
        "- Partitions: **45 development** and **45 held-out** questions.",
        "- Language distribution: **30 English, 30 Kiswahili, 30 code-switched**.",
        f"- Groups with active parent evidence mapped: **{len(mapped)}/30**.",
        f"- Unique mapped active parent chunks: **{validation['mapped_unique_parent_chunks']}**.",
        f"- Original DOCX files matching indexed extracted content: **{validation['verified_source_documents']}**.",
        f"- Behaviour-only groups excluded from retrieval/context recall: **{validation['behavior_only_groups']}**.",
        f"- Blank human partition-decision cells retained: **{validation['held_out_human_partition_decisions_blank']}**.",
        "- Dataset status: **AI-assisted validated and researcher-authorised**.",
        "- Human approval fields were not inferred or populated.",
        "",
        "## Verification method",
        "",
        "All eight workbook sheets were read. Active parent payloads were read directly from the",
        "active Chroma collection without issuing a retrieval query. Each indexed extracted-content",
        "version was recomputed from its original DOCX; raw-file SHA-256 digests were also recorded.",
        "Evidence mappings use",
        "reviewed workbook decisions and explicit policy-text anchors; no retrieval rank or score",
        "was used as a relevance label.",
        "",
        "Reference answers remain AI-assisted annotations. Researcher authorisation is recorded",
        "separately from the intentionally null human-review approval fields.",
        "",
        "## Integrity and leakage checks",
        "",
        f"- Exact normalized duplicate questions: **{len(validation['exact_duplicate_questions'])}**.",
        f"- Semantic groups shared across partitions: **{len(validation['semantic_group_overlap'])}**.",
        "- Held-out overlap findings from the workbook are preserved in each held-out group.",
        f"- Historical optimisation artefacts scanned: **{len(held_out['historical_leakage_audit']['historical_files_scanned'])}**.",
        f"- Current held-out cases checked against historical questions: **{held_out['historical_leakage_audit']['current_held_out_cases_checked']}**.",
        f"- Exact normalized matches to historical questions: **{len(held_out['historical_leakage_audit']['exact_historical_matches'])}**.",
        "- Blank human leakage decisions remain blank; researcher-authorised retain decisions are recorded separately.",
        "- No held-out evaluation, parameter tuning, embedding, or provider call was performed.",
        "",
        "## Remaining unresolved annotations",
        "",
    ]
    for group in unresolved:
        lines.append(f"### {group['semantic_group']}")
        lines.append("")
        for item in group["unresolved_items"]:
            lines.append(f"- {item}")
        lines.append("")
    if not unresolved:
        lines.append("No unresolved evidence mappings or reference-answer annotations remain.")
        lines.append("")

    lines.extend((
        "## Outputs",
        "",
        f"- `{DEVELOPMENT_OUTPUT.relative_to(PROJECT_DIR)}`",
        f"- `{HELD_OUT_OUTPUT.relative_to(PROJECT_DIR)}`",
        "",
        "These private evaluation artefacts are excluded by `.gitignore` and are not imported by",
        "the runtime application.",
        "",
        "## Scope",
        "",
        "No system prompt, retrieval, chunking, context construction, model, frontend, OAG,",
        "Firestore, production data, or production index was changed. The generation implementation",
        "is frozen pending experimental evaluation.",
    ))
    REPORT_OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    workbook = load_workbook(WORKBOOK_PATH, data_only=True)
    if tuple(workbook.sheetnames) != EXPECTED_SHEETS:
        raise ValueError(
            f"Unexpected worksheet order/content: {workbook.sheetnames}",
        )

    parents, manifest = active_parent_chunks()
    document_versions = verify_document_versions(parents)
    if not all(item["matches_active_index"] for item in document_versions.values()):
        raise ValueError("One or more active index document versions do not match the DOCX files.")

    reviewed_ids = candidate_ids_from_workbook(workbook)
    development = build_partition(
        workbook,
        "Development",
        "development",
        parents,
        reviewed_ids,
        document_versions,
        manifest,
    )
    held_out = build_partition(
        workbook,
        "Held-out",
        "held_out",
        parents,
        reviewed_ids,
        document_versions,
        manifest,
    )
    historical_audit = historical_leakage_audit(held_out["cases"])
    held_out["historical_leakage_audit"] = historical_audit
    development["historical_leakage_audit"] = {
        "scope": "held_out_only",
        "historical_files_scanned": historical_audit["historical_files_scanned"],
    }
    validation = validate_datasets(development, held_out)
    if not validation["valid"]:
        raise ValueError("Dataset validation failed: " + "; ".join(validation["errors"]))

    development["validation"] = validation
    held_out["validation"] = validation
    DEVELOPMENT_OUTPUT.write_text(
        json.dumps(development, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    HELD_OUT_OUTPUT.write_text(
        json.dumps(held_out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(development, held_out, validation)
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
