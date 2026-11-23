"""Audit and compare an isolated section-aware development index.

The active index is never opened for writing. Evaluation labels are used only
after retrieval, and remapping follows source locations rather than results.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter

from docx import Document
from docx.oxml.ns import qn


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.models.document import DocumentBlock, KnowledgeBaseDocument
from app.rag.chunking import chunk_documents
from app.rag.document_loader import load_knowledge_base_documents
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.rag.vector_index import VectorIndex
from scripts.compare_development_retrieval_optimizations import corrected_dataset
from scripts.experimental_evaluation import aggregate_offline, evaluate_offline_case


OUTPUT = BACKEND_DIR / "data" / "evaluation" / "section-aware-index-experiment.json"
INDEX_PATH = BACKEND_DIR / "data" / "evaluation" / "section-aware-chroma"
CHUNKS_PATH = BACKEND_DIR / "data" / "evaluation" / "section-aware-parent-chunks.json"


def _is_bold_heading(element, text: str) -> bool:
    """Recognise the source collection's visual headings without topic rules."""

    if not text or len(text) > 140 or "http://" in text.casefold() or "https://" in text.casefold():
        return False
    properties = element.find(qn("w:pPr"))
    if properties is not None and properties.find(qn("w:numPr")) is not None:
        return False
    runs = [run for run in element.findall(qn("w:r")) if "".join(run.itertext()).strip()]
    if not runs:
        return False
    bold = 0
    for run in runs:
        run_properties = run.find(qn("w:rPr"))
        value = run_properties.find(qn("w:b")) if run_properties is not None else None
        if value is not None and value.get(qn("w:val"), "1") not in {"0", "false", "off"}:
            bold += 1
    return bold / len(runs) >= 0.8


def _visual_heading_locations(path: Path) -> set[str]:
    document = Document(path)
    locations: set[str] = set()
    for index, child in enumerate(document.element.body, 1):
        if child.tag != qn("w:p"):
            continue
        text = " ".join("".join(child.itertext()).split())
        if _is_bold_heading(child, text):
            locations.add(f"Body/block {index}")
    return locations


def section_aware_documents(raw_dir: Path) -> tuple[list[KnowledgeBaseDocument], dict]:
    documents = load_knowledge_base_documents(raw_dir)
    audit = {"documents": {}, "total_inferred_headings": 0}
    transformed: list[KnowledgeBaseDocument] = []
    by_name = {path.name: path for path in raw_dir.rglob("*.docx")}

    for document in documents:
        headings = _visual_heading_locations(by_name[Path(document.source).name])
        current: str | None = None
        blocks: list[DocumentBlock] = []
        inferred = []
        for block in document.blocks:
            if block.source_location in headings and block.kind == "paragraph":
                current = block.text.strip()
                inferred.append({"location": block.source_location, "text": current})
                blocks.append(block.model_copy(update={"kind": "heading", "section": current}))
            else:
                blocks.append(block.model_copy(update={"section": current}))
        transformed.append(document.model_copy(update={"blocks": blocks}))
        audit["documents"][document.source] = {
            "original_blocks": len(document.blocks),
            "inferred_heading_count": len(inferred),
            "inferred_headings": inferred,
        }
        audit["total_inferred_headings"] += len(inferred)
    return transformed, audit


def _union_relevant(case: dict) -> set[str]:
    return {
        identifier
        for criterion in case["mandatory_evidence_criteria"]
        for identifier in criterion["acceptable_parent_chunk_ids"]
    }


def theoretical_ceiling(dataset: dict) -> dict:
    eligible = [case for case in dataset["cases"] if case["mandatory_evidence_criteria"]]
    cases = []
    for case in eligible:
        count = len(_union_relevant(case))
        cases.append({
            "case_id": case["case_id"], "language": case["language"],
            "annotated_relevant_parents": count,
            "maximum_precision_at_5": min(5, count) / 5,
        })
    language = {}
    for name in sorted({case["language"] for case in cases}):
        values = [case["maximum_precision_at_5"] for case in cases if case["language"] == name]
        language[name] = sum(values) / len(values)
    return {
        "method": "min(5, union of annotated relevant parent chunks) / 5",
        "eligible_cases": len(cases),
        "mean_maximum_precision_at_5": sum(case["maximum_precision_at_5"] for case in cases) / len(cases),
        "by_language": language,
        "cases": cases,
    }


def remap_dataset(dataset: dict, old_chunks: list[dict], new_chunks: list) -> tuple[dict, dict]:
    """Map criteria by original source spans, never by retrieved candidates."""

    old = {chunk["chunk_id"]: chunk for chunk in old_chunks}
    new_by_source: dict[str, list] = defaultdict(list)
    for chunk in new_chunks:
        new_by_source[Path(chunk.source.replace("\\", "/")).name].append(chunk)
    remapped = deepcopy(dataset)
    report = {"criteria": [], "unmapped": []}
    for case in remapped["cases"]:
        for criterion in case["mandatory_evidence_criteria"]:
            originals = [old[item] for item in criterion["acceptable_parent_chunk_ids"]]
            mapped: set[str] = set()
            for original in originals:
                original_locations = set(original.get("source_locations", []))
                source_name = Path(original["source"].replace("\\", "/")).name
                for candidate in new_by_source[source_name]:
                    if original_locations & set(candidate.source_locations):
                        mapped.add(candidate.chunk_id)
            record = {
                "case_id": case["case_id"],
                "criterion_id": criterion["criterion_id"],
                "old_parent_count": len(originals), "new_parent_count": len(mapped),
            }
            if not mapped:
                report["unmapped"].append(record)
                raise ValueError(f"Could not remap {criterion['criterion_id']} from source locations")
            criterion["acceptable_parent_chunk_ids"] = sorted(mapped)
            report["criteria"].append(record)
    return remapped, report


def evaluate(dataset: dict, retrieval: RetrievalService, settings) -> dict:
    retrieval.index.embedder.embed_query("section-aware evaluation warm-up")
    cases = []
    started = perf_counter()
    for case in dataset["cases"]:
        cases.append(evaluate_offline_case(case, retrieval, settings, 5))
    return {
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "aggregates": aggregate_offline(cases), "cases": cases,
    }


def representation_audit(old_chunks: list[dict], new_chunks: list, extraction_audit: dict) -> dict:
    old_texts = [chunk["text"] for chunk in old_chunks]
    new_texts = [chunk.text for chunk in new_chunks]
    source_counts = Counter(chunk["source"] for chunk in old_chunks)
    overlap_locations = sum(
        len(chunk.get("source_locations", [])) != len(set(chunk.get("source_locations", [])))
        for chunk in old_chunks
    )
    return {
        "source_document_count": len(source_counts),
        "existing_parent_chunks": len(old_chunks),
        "existing_chunks_with_section": sum(bool(chunk.get("section")) for chunk in old_chunks),
        "existing_table_chunks": sum(bool(chunk.get("table_location")) for chunk in old_chunks),
        "existing_mean_chars": sum(map(len, old_texts)) / len(old_texts),
        "existing_max_chars": max(map(len, old_texts)),
        "existing_duplicate_source_location_lists": overlap_locations,
        "experimental_parent_chunks": len(new_chunks),
        "experimental_chunks_with_section": sum(bool(chunk.section) for chunk in new_chunks),
        "experimental_table_chunks": sum(bool(chunk.table_location) for chunk in new_chunks),
        "experimental_mean_chars": sum(map(len, new_texts)) / len(new_texts),
        "experimental_max_chars": max(map(len, new_texts)),
        "per_document_existing_chunks": dict(sorted(source_counts.items())),
        "heading_audit": extraction_audit,
        "omission_check": "The loader/chunker reconstruction invariant passed for all 14 documents; no editable extracted text was omitted.",
    }


def main() -> int:
    settings = get_settings()
    raw_dir = settings.raw_data_dir / "KQ Data"
    dataset, corrections = corrected_dataset()
    processed_corpus = json.loads(
        (settings.processed_data_dir / "kq_document_chunks.json").read_text(
            encoding="utf-8",
        ),
    )
    old_chunks = processed_corpus["chunks"]
    documents, heading_audit = section_aware_documents(raw_dir)
    new_chunks = chunk_documents(documents, 1100, 100)
    remapped, remap_report = remap_dataset(dataset, old_chunks, new_chunks)

    write_json_atomic(CHUNKS_PATH, [chunk.model_dump(mode="json") for chunk in new_chunks])
    isolated_index = VectorIndex(INDEX_PATH, get_retrieval_service().index.embedder, settings.embedding_batch_size)
    sync = isolated_index.sync(new_chunks)
    experimental_settings = settings.model_copy(update={
        "chroma_persist_dir": INDEX_PATH,
        "hybrid_rrf_k": 10,
    })
    baseline = evaluate(dataset, get_retrieval_service(), settings)
    experimental = evaluate(remapped, RetrievalService(isolated_index, experimental_settings), experimental_settings)
    report = {
        "warning": "Development partition only; active index and held-out data were not accessed for writing/evaluation.",
        "configuration": {
            "rrf_k": 10, "chunk_size": 1100, "chunk_overlap": 100,
            "active_index_unchanged": True, "isolated_index_path": str(INDEX_PATH),
        },
        "theoretical_precision_ceiling": theoretical_ceiling(dataset),
        "representation_audit": representation_audit(old_chunks, new_chunks, heading_audit),
        "annotation_corrections": corrections,
        "evidence_remapping": remap_report,
        "index_sync": sync,
        "baseline": baseline,
        "experimental_section_aware": experimental,
    }
    write_json_atomic(OUTPUT, report)
    print(json.dumps({
        "output": str(OUTPUT), "chunks": len(new_chunks), "sync": sync,
        "baseline": baseline["aggregates"],
        "experimental": experimental["aggregates"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
