"""Evaluate corpus-derived bilingual metadata without external translation."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from time import perf_counter


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.rag.vector_index import VectorIndex
from scripts.advanced_multilingual_retrieval_experiment import (
    diagnostics,
    directory_size,
    evaluate,
    source_headings,
    summarize,
)
from scripts.compare_development_retrieval_optimizations import corrected_dataset


OUTPUT = BACKEND_DIR / "data" / "evaluation" / "bilingual-knowledge-base-retrieval-experiment.json"
INDEX_PATH = BACKEND_DIR / "data" / "evaluation" / "bilingual-metadata-chroma"
ARTEFACT = BACKEND_DIR / "data" / "evaluation" / "ai-assisted-kiswahili-search-metadata.json"

# Offline AI-assisted development metadata. Terms are policy concepts present
# in the source corpus, not evaluation questions or answers.
CATEGORY_SW = {
    "Baggage Allowance Information": "Taarifa za kiwango cha mizigo",
    "Baggage Conditions Rules": "Masharti na kanuni za mizigo",
    "Baggage Restrictions": "Vizuizi vya mizigo",
    "Bookings and Self Rebooking": "Nafasi za safari na kujibadilishia nafasi",
    "Check in": "Kuingia kwenye safari",
    "Conditions of Carriage": "Masharti ya usafirishaji",
    "Contact": "Mawasiliano",
    "Delayed or Lost or Damaged Baggage": "Mizigo iliyochelewa, kupotea au kuharibika",
    "FAQs": "Maswali yanayoulizwa mara kwa mara",
    "Flight upgrade": "Kupandisha daraja la safari",
    "Online Refunds": "Marejesho ya fedha mtandaoni",
    "Payments": "Malipo",
    "Pet Carriage": "Usafirishaji wa wanyama kipenzi",
    "Special Care": "Huduma maalumu",
}

TERMS = (
    ("baggage allowance", "kiwango cha mizigo"),
    ("checked baggage", "mizigo ya kupakiwa"),
    ("cabin baggage", "mizigo ya mkononi"),
    ("excess baggage", "mizigo inayozidi kiwango"),
    ("lost baggage", "mizigo iliyopotea"),
    ("damaged baggage", "mizigo iliyoharibika"),
    ("delayed baggage", "mizigo iliyochelewa"),
    ("business class", "daraja la biashara"),
    ("economy class", "daraja la kawaida"),
    ("reservation", "nafasi ya safari"),
    ("booking", "nafasi ya safari"),
    ("rebooking", "kubadilisha nafasi ya safari"),
    ("ticket", "tiketi"),
    ("refund", "marejesho ya fedha"),
    ("payment", "malipo"),
    ("check-in", "kuingia kwenye safari"),
    ("boarding", "kupanda ndege"),
    ("name correction", "kusahihisha jina"),
    ("travel documents", "hati za safari"),
    ("passport", "pasipoti"),
    ("visa", "viza"),
    ("wheelchair", "kiti cha magurudumu"),
    ("reduced mobility", "uwezo mdogo wa kutembea"),
    ("special assistance", "msaada maalumu"),
    ("unaccompanied minor", "mtoto anayesafiri bila msindikizaji"),
    ("pregnancy", "ujauzito"),
    ("expectant mother", "mama mjamzito"),
    ("pet", "mnyama kipenzi"),
    ("service dog", "mbwa wa huduma"),
    ("flight cancellation", "kufutwa kwa safari"),
    ("flight delay", "kuchelewa kwa safari"),
    ("contact center", "kituo cha mawasiliano"),
    ("customer support", "huduma kwa wateja"),
    ("prohibited items", "vitu vilivyopigwa marufuku"),
    ("restricted items", "vitu vyenye vizuizi"),
    ("fees", "ada"),
    ("charges", "gharama"),
    ("conditions", "masharti"),
    ("exception", "hali isiyofuata kanuni ya kawaida"),
)


def representations(raw_chunks: list[dict], headings: dict[str, list[tuple[int, str]]]):
    chunks = [DocumentChunk.model_validate(item) for item in raw_chunks]
    search = {}
    metadata = []
    for chunk_index, chunk in enumerate(chunks):
        category_sw = CATEGORY_SW[chunk.category]
        found = [
            {"english": english, "kiswahili": swahili}
            for english, swahili in TERMS
            if re.search(rf"\b{re.escape(english)}\b", chunk.text, re.IGNORECASE)
        ]
        name = Path(chunk.source.replace("\\", "/")).name
        positions = [
            int(match.group(1))
            for location in chunk.source_locations
            if (match := re.search(r"Body/block (\d+)", location))
        ]
        start = min(positions, default=0)
        prior = [item for item in headings[name] if item[0] <= start]
        english_heading = prior[-1][1] if prior else chunk.category
        terminology = "; ".join(item["kiswahili"] for item in found)
        bilingual_section = f"{english_heading} | {category_sw}"
        enriched = chunk.model_copy(update={"section": bilingual_section})
        prefix = (
            f"Document: {chunk.category} | {category_sw}\n"
            f"Section: {bilingual_section}"
        )
        if terminology:
            prefix += f"\nIstilahi za utafutaji: {terminology}"
        search[chunk.chunk_id] = [prefix + "\n\n" + chunk.text]
        metadata.append({
            "parent_chunk_id": chunk.chunk_id,
            "document": chunk.category,
            "document_title_kiswahili": category_sw,
            "source_heading_english": english_heading,
            "terminology": found,
            "status": "AI-assisted search metadata; not an official translation",
        })
        chunks[chunk_index] = enriched
    return chunks, search, metadata


def main() -> int:
    settings = get_settings()
    dataset, corrections = corrected_dataset()
    processed = json.loads((settings.processed_data_dir / "kq_document_chunks.json").read_text(encoding="utf-8"))
    headings = source_headings(settings.raw_data_dir / "KQ Data")
    chunks, search, metadata = representations(processed["chunks"], headings)
    base = get_retrieval_service()
    index = VectorIndex(INDEX_PATH, base.index.embedder, settings.embedding_batch_size)
    started = perf_counter()
    sync = index.sync(chunks, search_representations=search)
    indexing_ms = round((perf_counter() - started) * 1000, 3)
    service = RetrievalService(index, settings)
    baseline = evaluate(dataset, base, settings)
    bilingual = evaluate(dataset, service, settings)
    baseline["summary"] = summarize(baseline)
    bilingual["summary"] = summarize(bilingual)
    write_json_atomic(ARTEFACT, {
        "status": "AI-assisted search metadata; not official Kenya Airways translations",
        "coverage": {"parents": len(metadata), "documents": len(CATEGORY_SW)},
        "verification": "Terminology requires independent bilingual policy review before production use.",
        "parents": metadata,
    })
    report = {
        "warning": "Development only; no held-out data or external APIs accessed.",
        "configuration": {"rrf_k": 10, "authoritative_parents": len(chunks)},
        "annotation_corrections": corrections,
        "translation_status": {
            "bilingual_metadata": "completed_ai_assisted_unverified",
            "complete_kiswahili_passages": "not_executed",
            "reason": "No offline translation model or verified Kiswahili policy translation was available; rule substitution was not misrepresented as complete translation.",
        },
        "indexing": {"sync": sync, "indexing_ms": indexing_ms},
        "variants": {
            "english_only_baseline": baseline,
            "english_with_bilingual_headings_and_terminology": bilingual,
            "english_and_complete_kiswahili": None,
        },
        "candidate_pool_diagnostics": {
            "english_only_baseline": diagnostics(dataset, base),
            "english_with_bilingual_headings_and_terminology": diagnostics(dataset, service),
        },
        "storage_bytes": {
            "active": directory_size(settings.chroma_persist_dir),
            "bilingual_metadata": directory_size(INDEX_PATH),
        },
        "estimated_raw_vector_memory_bytes": {
            "active": 189 * 384 * 4,
            "bilingual_metadata": sync["count"] * 384 * 4,
        },
    }
    write_json_atomic(OUTPUT, report)
    print(json.dumps({
        "baseline": baseline["aggregates"],
        "bilingual_metadata": bilingual["aggregates"],
        "translation_status": report["translation_status"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
