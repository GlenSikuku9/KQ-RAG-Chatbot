"""Validate translated DOCX files and evaluate a complete bilingual index.

The script is intentionally linear: validate, align, index, evaluate, report.
Only the development partition is loaded.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import statistics
import sys
from time import perf_counter

from docx import Document


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.models.retrieval import RetrievalRequest
from app.rag.document_loader import load_knowledge_base_document
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.rag.vector_index import VectorIndex
from scripts.advanced_multilingual_retrieval_experiment import (
    directory_size,
    evaluate,
    summarize,
)
from scripts.compare_development_retrieval_optimizations import (
    Variant,
    VariantRetrieval,
    corrected_dataset,
)


TRANSLATION_DIR = BACKEND_DIR / "data" / "translations" / "kiswahili"
INDEX_PATH = BACKEND_DIR / "data" / "evaluation" / "complete-bilingual-chroma"
OUTPUT = BACKEND_DIR / "data" / "evaluation" / "complete-bilingual-retrieval-experiment.json"
ALIGNMENT_OUTPUT = BACKEND_DIR / "data" / "evaluation" / "complete-bilingual-alignment.json"


def document_shape(document: Document) -> dict:
    """Capture structure without depending on translated text length."""

    return {
        "paragraphs": len(document.paragraphs),
        "tables": [
            {
                "rows": len(table.rows),
                "columns": [
                    len(row.cells)
                    for row in table.rows
                ],
            }
            for table in document.tables
        ],
    }


def document_text(document: Document) -> str:
    """Collect body and table text for invariant checks."""

    values = [
        paragraph.text
        for paragraph in document.paragraphs
    ]
    values.extend(
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )
    return "\n".join(values)


def invariant_tokens(text: str) -> Counter:
    """Extract values that translation should preserve verbatim."""

    tokens = re.findall(
        r"https?://\S+|\+\d[\d\s-]+|\b\d+(?:[.,]\d+)?(?:(?i:kg|kgs|cm|cms|lb|lbs|hours?|minutes?|days?|%))?\b|\b(?:SSR|EMD|IATA|DCS|MSC|PNR|TSA|KQ|USA|UK|WCHR|WCHS|WCHC|WCBD|WCBW|WCLB|WCMP|PETC|AVIH|UMNR|SPEQ|TBA)\b",
        text,
    )
    return Counter(token.rstrip(".,);:") for token in tokens)


def translated_block_map(document) -> dict[str, str]:
    """Map structural DOCX locations to translated text."""

    values: dict[str, str] = {}
    for block in document.blocks:
        values[block.source_location] = block.text
        for row in block.rows:
            values[
                f"{block.source_location}/row {row.row_index}"
            ] = row.text
    return values


def translated_parent_text(
    parent: DocumentChunk,
    locations: dict[str, str],
) -> str:
    """Align a translated search passage using original structural locations."""

    parts: list[str] = []
    for location in parent.source_locations:
        value = locations.get(location)
        if value and value not in parts:
            parts.append(value)
    if not parts:
        raise ValueError(
            f"No translated structural text for parent {parent.chunk_id}.",
        )
    return "\n\n".join(parts)


def validate_and_align(settings):
    """Validate all pairs and create two representations per English parent."""

    processed = json.loads(
        (
            settings.processed_data_dir
            / "kq_document_chunks.json"
        ).read_text(encoding="utf-8"),
    )
    parents = [
        DocumentChunk.model_validate(value)
        for value in processed["chunks"]
    ]

    translated_locations: dict[str, dict[str, str]] = {}
    validation: list[dict] = []

    for english_path in sorted(
        (settings.raw_data_dir / "KQ Data").glob("*.docx"),
    ):
        translated_path = (
            TRANSLATION_DIR
            / f"{english_path.stem} - SW.docx"
        )
        if not translated_path.exists():
            raise FileNotFoundError(
                f"Missing translation for {english_path.name}.",
            )

        english_docx = Document(english_path)
        translated_docx = Document(translated_path)
        english_shape = document_shape(english_docx)
        translated_shape = document_shape(translated_docx)

        english_values = invariant_tokens(
            document_text(english_docx),
        )
        translated_values = invariant_tokens(
            document_text(translated_docx),
        )

        translated_document = load_knowledge_base_document(
            translated_path,
            TRANSLATION_DIR,
        )
        translated_locations[english_path.name] = (
            translated_block_map(translated_document)
        )

        validation.append(
            {
                "english": english_path.name,
                "kiswahili": translated_path.name,
                "structure_matches": (
                    english_shape == translated_shape
                ),
                "english_shape": english_shape,
                "kiswahili_shape": translated_shape,
                "missing_invariants": list(
                    (english_values - translated_values).elements(),
                ),
                "additional_invariants": list(
                    (translated_values - english_values).elements(),
                ),
                "translation_version": translated_document.version,
            },
        )

    representations: dict[str, list[str]] = {}
    alignment: list[dict] = []

    for parent in parents:
        source_name = Path(
            parent.source.replace("\\", "/"),
        ).name
        translated_text = translated_parent_text(
            parent,
            translated_locations[source_name],
        )
        representations[parent.chunk_id] = [
            parent.text,
            translated_text,
        ]
        alignment.append(
            {
                "parent_chunk_id": parent.chunk_id,
                "english_source": parent.source,
                "source_locations": parent.source_locations,
                "english_text": parent.text,
                "kiswahili_search_text": translated_text,
            },
        )

    return parents, representations, validation, alignment


class LanguageAwareRetrieval:
    """Protect English queries while allowing bilingual Swahili retrieval."""

    def __init__(
        self,
        english: RetrievalService,
        bilingual: RetrievalService,
    ) -> None:
        self.english = english
        self.bilingual = bilingual
        self.settings = bilingual.settings
        self.index = bilingual.index

    def retrieve(self, request: RetrievalRequest):
        # English keeps the proven active representation. Swahili and mixed
        # queries retain access to both translated and English passages.
        if request.language == "en":
            return self.english.retrieve(request)
        return self.bilingual.retrieve(request)


def candidate_diagnostics(dataset: dict, retrieval) -> dict:
    """Measure evidence availability before the final top-five cutoff."""

    rows = []
    for case in dataset["cases"]:
        criteria = case["mandatory_evidence_criteria"]
        if not criteria:
            continue

        response = retrieval.retrieve(
            RetrievalRequest(
                question=case["question"],
                resolved_question=case.get("resolved_question"),
                language=case["language"],
                top_k=20,
            ),
        )
        identifiers = {
            result.chunk.chunk_id
            for result in response.results
        }
        satisfied = sum(
            bool(
                identifiers
                & set(criterion["acceptable_parent_chunk_ids"])
            )
            for criterion in criteria
        )
        rows.append(
            {
                "case_id": case["case_id"],
                "language": case["language"],
                "criterion_recall_at_20": (
                    satisfied / len(criteria)
                ),
            },
        )

    def mean(language: str | None = None) -> float:
        selected = [
            row["criterion_recall_at_20"]
            for row in rows
            if language is None or row["language"] == language
        ]
        return sum(selected) / len(selected)

    return {
        "mean": mean(),
        "by_language": {
            language: mean(language)
            for language in ("en", "sw", "mixed")
        },
        "zero_recall_cases": sum(
            row["criterion_recall_at_20"] == 0
            for row in rows
        ),
        "cases": rows,
    }


def warmed_latency(dataset: dict, retrieval) -> dict:
    """Run repeated warmed retrieval passes under identical conditions."""

    requests = [
        RetrievalRequest(
            question=case["question"],
            resolved_question=case.get("resolved_question"),
            language=case["language"],
            top_k=5,
        )
        for case in dataset["cases"]
    ]
    retrieval.retrieve(requests[0])

    pass_means = []
    for _ in range(3):
        started = perf_counter()
        for request in requests:
            retrieval.retrieve(request)
        elapsed = (perf_counter() - started) * 1000
        pass_means.append(elapsed / len(requests))

    return {
        "passes": 3,
        "mean_ms": sum(pass_means) / len(pass_means),
        "median_pass_mean_ms": statistics.median(pass_means),
        "pass_means_ms": pass_means,
    }


def main() -> int:
    settings = get_settings()
    dataset, corrections = corrected_dataset()

    parents, representations, validation, alignment = (
        validate_and_align(settings)
    )
    if not all(item["structure_matches"] for item in validation):
        raise ValueError(
            "Translated DOCX structure does not match every English source.",
        )

    write_json_atomic(
        ALIGNMENT_OUTPUT,
        {
            "status": "AI-assisted; not independently human verified",
            "documents": validation,
            "parents": alignment,
        },
    )

    active = get_retrieval_service()
    bilingual_index = VectorIndex(
        INDEX_PATH,
        active.index.embedder,
        settings.embedding_batch_size,
    )

    indexing_started = perf_counter()
    sync = bilingual_index.sync(
        parents,
        search_representations=representations,
    )
    indexing_ms = (
        perf_counter() - indexing_started
    ) * 1000

    bilingual = RetrievalService(
        bilingual_index,
        settings,
    )
    language_aware = LanguageAwareRetrieval(
        active,
        bilingual,
    )
    bilingual_without_expansion = VariantRetrieval(
        bilingual,
        Variant(
            "complete_bilingual_without_expansion",
            rrf_k=10,
            use_expansion=False,
        ),
    )

    services = {
        "english_only_baseline": active,
        "unified_bilingual": bilingual,
        "language_aware_bilingual": language_aware,
        "unified_bilingual_without_query_expansion": (
            bilingual_without_expansion
        ),
    }

    variants = {}
    for name, service in services.items():
        result = evaluate(
            dataset,
            service,
            settings,
        )
        result["summary"] = summarize(result)
        result["candidate_pool"] = candidate_diagnostics(
            dataset,
            service,
        )
        result["warmed_latency"] = warmed_latency(
            dataset,
            service,
        )
        variants[name] = result

    manifest = bilingual_index._manifest()
    report = {
        "warning": "Development only; held-out data was not accessed.",
        "translation_status": (
            "AI-assisted; not independently human verified"
        ),
        "configuration": {
            "rrf_k": 10,
            "authoritative_parent_count": len(parents),
            "representations_per_parent": 2,
            "active_index_overwritten": False,
        },
        "annotation_corrections": corrections,
        "document_validation": validation,
        "indexing": {
            "sync": sync,
            "duration_ms": indexing_ms,
            "stored_segments": manifest.count if manifest else None,
            "directory_bytes": directory_size(INDEX_PATH),
            "raw_vector_memory_estimate_bytes": (
                manifest.count * 384 * 4
                if manifest
                else None
            ),
        },
        "variants": variants,
    }
    write_json_atomic(OUTPUT, report)

    print(
        json.dumps(
            {
                name: {
                    "aggregates": value["aggregates"],
                    "candidate_pool": value["candidate_pool"],
                    "summary": value["summary"],
                    "warmed_latency": value["warmed_latency"],
                }
                for name, value in variants.items()
            },
            indent=2,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
