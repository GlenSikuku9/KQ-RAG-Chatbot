"""Run the fixed KQ-RAG-Eval-200 retrieval and three-model experiment.

The workflow deliberately separates evidence mapping from retrieval rankings,
persists retrieval before any provider request, and checkpoints every
case/model outcome. It never uses reference answers in generation prompts.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import secrets
import statistics
import sys
from time import perf_counter, sleep
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import GenerationModelSettings, get_settings
from app.models.generation import GroundedAnswerRequest
from app.models.retrieval import ConversationContextMessage
from app.rag.chunking import chunk_documents
from app.rag.context import build_context
from app.rag.document_loader import (
    load_knowledge_base_document,
    load_knowledge_base_documents,
)
from app.rag.generation import GenerationProviderError, GenerationService
from app.rag.grounded_generation import SYSTEM_INSTRUCTION
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.rag.vector_index import VectorIndex
from app.services.conversation_context import resolve_conversation_query
from scripts.advanced_multilingual_retrieval_experiment import directory_size
from scripts.complete_bilingual_retrieval_experiment import (
    translated_block_map,
    translated_parent_text,
)
from scripts.experimental_evaluation import (
    CANDIDATE_MODEL_IDS,
    aggregate_offline,
    evaluate_offline_case,
)
from scripts.optimize_bilingual_chunking import chunk_statistics
from scripts.optimize_bilingual_ranking_context import SelectiveMixedRouter


EVALUATION_DIR = BACKEND_DIR / "data" / "evaluation" / "LLM evaluation"
DATASET_PATH = EVALUATION_DIR / "KQ-RAG-Eval-200.json"
INDEX_DIR = EVALUATION_DIR / "final-bilingual-index-1800-360"
MAPPING_PATH = EVALUATION_DIR / "final_evidence_mapping.json"
RETRIEVAL_PATH = EVALUATION_DIR / "final_retrieval_results.json"
RESULTS_PATH = EVALUATION_DIR / "final_llm_evaluation_results.json"
CSV_PATH = EVALUATION_DIR / "final_llm_evaluation_results.csv"
IDENTITY_PATH = EVALUATION_DIR / ".final_model_identity_map.json"
TRANSLATION_DIR = BACKEND_DIR / "data" / "translations" / "kiswahili"

TARGET_SIZE = 1800
MAX_OVERLAP = 360
TOP_K = 5
RRF_K = 10

# These evaluation-only upstream IDs deliberately differ from the production
# registry. The two OpenRouter suffixes select the zero-price variants exactly;
# no production setting or active model selection is changed.
EXPECTED_UPSTREAM_MODELS = {
    "openrouter_gemma_26b": "google/gemma-4-26b-a4b-it:free",
    "openrouter_gemma_31b": "google/gemma-4-31b-it:free",
    "groq_gpt_oss_120b": "openai/gpt-oss-120b",
}


class FreeTierEvaluationGenerationService(GenerationService):
    """Apply a hard zero-price boundary to evaluation-only OpenRouter calls."""

    def generate(self, prompt, model_id=None, system_instruction=None):
        """Honor short provider-directed waits without changing production behavior."""

        for quota_attempt in range(10):
            try:
                return super().generate(prompt, model_id, system_instruction)
            except GenerationProviderError as error:
                retry_after = error.retry_after_seconds
                if (
                    error.status_code != 429
                    or retry_after is None
                    or retry_after > 60
                    or quota_attempt == 9
                ):
                    raise
                sleep(max(1.0, retry_after + 1.0))
        raise RuntimeError("Unreachable quota retry state.")

    def _openrouter(self, model, prompt, system):
        if not model.upstream_model.endswith(":free"):
            raise GenerationProviderError(
                400,
                "Evaluation refused a non-free OpenRouter model identifier.",
            )
        messages = (
            ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}]
        )
        data = self._post(
            "https://openrouter.ai/api/v1/chat/completions",
            model,
            {
                "model": model.upstream_model,
                "messages": messages,
                "max_tokens": model.max_output_tokens,
                # A zero ceiling makes the request fail if no zero-price endpoint
                # satisfies it. Provider failover remains within this exact free
                # model; no alternative-model list is supplied.
                "provider": {
                    "allow_fallbacks": True,
                    "max_price": {"prompt": 0, "completion": 0},
                },
            },
            {"Authorization": f"Bearer {model.api_key}"},
        )
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise GenerationProviderError(
                502,
                "The selected AI provider returned an invalid response.",
            ) from error
        return self._response(model, self._text(text), data.get("id"))


class ReplayContextService:
    """Replay one precomputed context through the production answer service."""

    def __init__(self) -> None:
        self.prepared = None

    def prepare(self, request):
        if self.prepared is None:
            raise RuntimeError("No final-evaluation context is selected.")
        return self.prepared


def normalized(text: str) -> str:
    """Normalize punctuation and whitespace without changing numeric values."""

    value = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", text.casefold())
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    number_words = {
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
    }
    return " ".join(number_words.get(token, token) for token in value.split())


def percentile(values: list[float], fraction: float) -> float | None:
    """Return the observed nearest-rank percentile."""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def load_dataset() -> dict[str, Any]:
    """Load and strictly validate the immutable held-out interchange dataset."""

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = dataset.get("cases", [])
    errors = []
    if dataset.get("split") != "held_out":
        errors.append("Dataset split is not held_out.")
    if len(cases) != 200 or dataset.get("question_count") != 200:
        errors.append("Dataset must contain exactly 200 cases.")
    identifiers = [case.get("id") for case in cases]
    if len(set(identifiers)) != len(identifiers):
        errors.append("Case identifiers are not unique.")
    if Counter(case.get("language") for case in cases) != {
        "en": 67,
        "sw": 67,
        "mixed": 66,
    }:
        errors.append("Language distribution differs from 67/67/66.")
    if Counter(case.get("expected_behavior") for case in cases) != {
        "answer": 180,
        "fallback": 10,
        "clarify": 10,
    }:
        errors.append("Expected-behaviour distribution is invalid.")
    if len({case.get("semantic_group") for case in cases}) != 160:
        errors.append("Semantic-group count is not 160.")
    for case in cases:
        required = case.get("required_evidence", [])
        applicable = case.get("retrieval_metrics_applicable") is True
        if applicable != bool(required):
            errors.append(f"Evidence applicability mismatch: {case.get('id')}")
    if errors:
        raise ValueError(" ".join(errors))
    return dataset


def translation_locations(settings) -> dict[str, dict[str, str]]:
    """Load the verified block-level Kiswahili alignment for all 14 files."""

    locations = {}
    for english_path in sorted(settings.raw_data_dir.rglob("*.docx")):
        translated_path = (
            TRANSLATION_DIR
            / f"{english_path.stem} - SW.docx"
        )
        if not translated_path.exists():
            raise FileNotFoundError(f"Missing translation: {translated_path}")
        translated = load_knowledge_base_document(
            translated_path,
            TRANSLATION_DIR,
        )
        locations[english_path.name] = translated_block_map(translated)
    if len(locations) != 14:
        raise ValueError(f"Expected 14 aligned documents, found {len(locations)}.")
    return locations


def bilingual_representations(chunks, locations) -> dict[str, list[str]]:
    """Associate English and Kiswahili search text with one English parent."""

    result = {}
    for chunk in chunks:
        document = Path(chunk.source.replace("\\", "/")).name
        result[chunk.chunk_id] = [
            chunk.text,
            translated_parent_text(chunk, locations[document]),
        ]
    return result


def criterion_fact_fallback(criterion: dict[str, Any], candidates) -> list:
    """Resolve formatting-broken anchors only when the full fact is present.

    This conservative fallback requires every number from the criterion and a
    high share of its meaningful words in the same authoritative parent.
    """

    fact = normalized(criterion["criterion"])
    numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", fact))
    stop = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "is", "it", "of", "on", "or", "the", "to", "up", "with",
        "includes", "include", "class", "flights", "flight", "kq", "operated",
    }
    words = {word for word in fact.split() if len(word) > 2 and word not in stop}
    resolved = []
    for chunk in candidates:
        text = normalized(chunk.text)
        text_words = set(text.split())
        number_match = all(number in text_words for number in numbers)
        word_ratio = len(words & text_words) / len(words) if words else 0.0
        if number_match and word_ratio >= 0.50:
            resolved.append(chunk)
    return resolved


def map_evidence(dataset: dict[str, Any], chunks) -> tuple[dict, dict]:
    """Map criteria from source anchors before any retrieval is executed."""

    by_document = defaultdict(list)
    for chunk in chunks:
        document = Path(chunk.source.replace("\\", "/")).name
        by_document[document].append(chunk)

    internal_cases = []
    audit = []
    unresolved = []
    for source_case in dataset["cases"]:
        criteria = []
        for source_criterion in source_case["required_evidence"]:
            matched = {}
            anchors = []
            expected_documents = set()
            for source in source_criterion["evidence_sources"]:
                document = source["document"]
                expected_documents.add(document)
                anchor = normalized(source["anchor_text"])
                anchors.append(source["anchor_text"])
                for chunk in by_document.get(document, []):
                    if anchor and anchor in normalized(chunk.text):
                        matched[chunk.chunk_id] = chunk

            method = "normalized_anchor_within_expected_document"
            if not matched:
                candidates = [
                    chunk
                    for document in expected_documents
                    for chunk in by_document.get(document, [])
                ]
                fallback = criterion_fact_fallback(source_criterion, candidates)
                matched = {chunk.chunk_id: chunk for chunk in fallback}
                method = "criterion_fact_match_within_expected_document"

            status = "mapped" if matched else "unresolved"
            if not matched:
                unresolved.append({
                    "case_id": source_case["id"],
                    "criterion_id": source_criterion["criterion_id"],
                    "criterion": source_criterion["criterion"],
                    "anchors": anchors,
                    "expected_documents": sorted(expected_documents),
                })
            criteria.append({
                "criterion_id": source_criterion["criterion_id"],
                "policy_text_locator": " | ".join(anchors),
                "requirement": "mandatory",
                "matching_rule": "any_of_equivalent_parent_chunks",
                "acceptable_parent_chunk_ids": sorted(matched),
                "status": status,
                "criterion_text": source_criterion["criterion"],
            })
            audit.append({
                "case_id": source_case["id"],
                "criterion_id": source_criterion["criterion_id"],
                "criterion": source_criterion["criterion"],
                "method": method,
                "status": status,
                "expected_documents": sorted(expected_documents),
                "anchors": anchors,
                "mapped_parent_chunk_ids": sorted(matched),
                "mapped_evidence_text": [
                    matched[parent_id].text
                    for parent_id in sorted(matched)
                ],
            })

        history = source_case.get("fixed_history") or []
        history_text = "\n".join(
            f"{'USER' if item['role'] == 'user' else 'ASSISTANT'}: {item['content']}"
            for item in history
        ) or None
        internal_cases.append({
            "case_id": source_case["id"],
            "partition": "held_out",
            "semantic_group": source_case["semantic_group"],
            "language": source_case["language"],
            "question": source_case["question"],
            "fixed_history": history_text,
            "expected_behavior": source_case["expected_behavior"],
            "reference_answer": source_case["reference_answer"],
            "metric_eligibility": {
                "retrieval_and_context_recall": (
                    "eligible"
                    if source_case["retrieval_metrics_applicable"]
                    else "ineligible"
                ),
                "answer_correctness": (
                    "eligible" if source_case["answerable"] else "ineligible"
                ),
            },
            "mandatory_evidence_criteria": criteria,
            "category": source_case["category"],
            "type": source_case["type"],
            "answerable": source_case["answerable"],
            "matched_multilingual": source_case["matched_multilingual"],
            "expected_source_documents": source_case["expected_source_documents"],
        })

    return {"cases": internal_cases}, {
        "schema_version": "kq-final-evidence-mapping-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mapping_performed_before_retrieval": True,
        "criteria_total": len(audit),
        "mapped": sum(item["status"] == "mapped" for item in audit),
        "unresolved": unresolved,
        "methods": dict(Counter(item["method"] for item in audit)),
        "criteria": audit,
    }


def request_for(case: dict[str, Any]) -> GroundedAnswerRequest:
    """Build the production request while preserving fixed conversation history."""

    history = []
    if case.get("fixed_history"):
        for line in case["fixed_history"].splitlines():
            role, _, content = line.partition(":")
            history.append(ConversationContextMessage(
                conversation_id=f"final-evaluation-{case['case_id']}",
                sender_type="customer" if role == "USER" else "chatbot",
                message_text=content.strip(),
            ))
    resolution = resolve_conversation_query(case["question"], history)
    return GroundedAnswerRequest(
        question=case["question"],
        language=case["language"],
        conversation_context=list(resolution.context),
        resolved_question=resolution.resolved_question,
        top_k=TOP_K,
    )


def retrieval_aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Reuse established aggregation and expose overall/language scopes."""

    return aggregate_offline(cases)


def build_fixed_pipeline(dataset: dict[str, Any]):
    """Build/reuse the isolated fixed bilingual index and map evidence."""

    settings = get_settings()
    documents = load_knowledge_base_documents(settings.raw_data_dir)
    chunks = chunk_documents(documents, TARGET_SIZE, MAX_OVERLAP)
    locations = translation_locations(settings)
    representations = bilingual_representations(chunks, locations)
    internal, mapping = map_evidence(dataset, chunks)
    if mapping["unresolved"]:
        raise ValueError(
            f"Unresolved evidence criteria: {len(mapping['unresolved'])}",
        )
    write_json_atomic(MAPPING_PATH, mapping)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    active = get_retrieval_service()
    index = VectorIndex(
        INDEX_DIR,
        active.index.embedder,
        settings.embedding_batch_size,
    )
    index_started = perf_counter()
    sync = index.sync(chunks, search_representations=representations)
    index_ms = round((perf_counter() - index_started) * 1000, 3)
    fixed_settings = settings.model_copy(update={
        "rag_chunk_size": TARGET_SIZE,
        "rag_chunk_overlap": MAX_OVERLAP,
        "hybrid_rrf_k": RRF_K,
    })
    english_index = VectorIndex(
        INDEX_DIR / "english",
        active.index.embedder,
        settings.embedding_batch_size,
    )
    english_sync = english_index.sync(chunks)
    bilingual = RetrievalService(index, fixed_settings)
    english = RetrievalService(english_index, fixed_settings)
    selective = SelectiveMixedRouter(english, bilingual)
    quality = chunk_statistics(
        chunks,
        representations,
        active.index.embedder,
        MAX_OVERLAP,
    )
    index_stats = {
        "source_documents_english": 14,
        "source_documents_kiswahili": 14,
        "authoritative_parent_chunks": len(chunks),
        "english_representations": len(chunks),
        "kiswahili_representations": len(chunks),
        "indexed_vector_segments": sync.get("count"),
        "bilingual_sync": sync,
        "english_sync": english_sync,
        "index_construction_ms": index_ms,
        "storage_bytes": directory_size(INDEX_DIR),
        "chunk_quality": quality,
    }
    return internal, chunks, selective, fixed_settings, index_stats


def run_retrieval(dataset: dict[str, Any]) -> tuple[dict, dict, Any, Any]:
    """Execute retrieval/context exactly once per held-out case."""

    internal, chunks, selective, settings, index_stats = build_fixed_pipeline(dataset)
    offline_cases = []
    prepared_objects = {}
    for number, case in enumerate(internal["cases"], 1):
        request = request_for(case)
        retrieval = selective.retrieve(request)
        context_started = perf_counter()
        prepared = build_context(retrieval, settings)
        context_ms = round((perf_counter() - context_started) * 1000, 3)
        prepared_objects[case["case_id"]] = (prepared, request)

        # Use the established serializer/metric implementation against the
        # exact same router. Its second retrieval is deterministic and local;
        # overwrite timings/context with the first controlled execution.
        row = evaluate_offline_case(case, selective, settings, TOP_K)
        selected_ids = [source.chunk.chunk_id for source in prepared.sources]
        row["retrieval"]["retrieval_ms"] = retrieval.retrieval_ms
        row["context"]["context_build_ms"] = context_ms
        row["context"]["decision"] = prepared.decision
        row["context"]["reason"] = prepared.reason
        row["context"]["message"] = prepared.message
        row["context"]["context_chars"] = prepared.context_chars
        row["context"]["context_tokens_estimate"] = prepared.context_tokens
        row["context"]["selected_parent_chunk_ids"] = selected_ids
        row["context"]["selected_contexts"] = [
            source.chunk.text for source in prepared.sources
        ]
        row["context"]["rendered_context"] = prepared.context
        offline_cases.append(row)
        print(f"[retrieval {number}/200] {case['case_id']}", flush=True)

    report = {
        "schema_version": "kq-final-retrieval-evaluation-v1",
        "run_id": "kq-rag-eval-200-fixed-1800-360",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "name": dataset["dataset_name"],
            "version": dataset["version"],
            "split": dataset["split"],
            "questions": len(dataset["cases"]),
            "language_counts": dataset["language_counts"],
        },
        "configuration": {
            "target_chunk_size": TARGET_SIZE,
            "maximum_overlap": MAX_OVERLAP,
            "rrf_k": RRF_K,
            "top_k": TOP_K,
            "language_routing": "selective_bilingual",
            "candidate_depth_dense": settings.hybrid_dense_candidates,
            "candidate_depth_lexical": settings.hybrid_lexical_candidates,
            "similarity_threshold": settings.context_min_similarity,
            "context_budget_tokens": settings.context_max_tokens,
            "context_budget_characters": settings.context_max_chars,
            "context_max_documents": settings.context_max_documents,
            "context_max_chunks_per_document": settings.context_max_chunks_per_document,
            "active_production_index_overwritten": False,
        },
        "index_statistics": index_stats,
        "evidence_mapping_file": str(MAPPING_PATH),
        "cases": offline_cases,
        "aggregates": retrieval_aggregate(offline_cases),
    }
    write_json_atomic(RETRIEVAL_PATH, report)
    return report, internal, prepared_objects, settings


def configured_models() -> tuple[dict[str, Any], FreeTierEvaluationGenerationService]:
    """Build an evaluation-only registry with exact free-tier identifiers."""

    settings = get_settings()
    configured = {model.model_id: model for model in settings.generation_models}
    evaluation_models = []
    metadata = {}
    for model_id in CANDIDATE_MODEL_IDS:
        model = configured.get(model_id)
        if model is None or not model.api_key:
            raise ValueError(f"Candidate is not configured: {model_id}")
        evaluation_model = GenerationModelSettings(
            model_id=model.model_id,
            provider=model.provider,
            upstream_model=EXPECTED_UPSTREAM_MODELS[model_id],
            api_key=model.api_key,
            timeout_seconds=model.timeout_seconds,
            max_output_tokens=model.max_output_tokens,
        )
        evaluation_models.append(evaluation_model)
        metadata[model_id] = {
            "provider": evaluation_model.provider,
            "upstream_model": evaluation_model.upstream_model,
            "max_output_tokens": evaluation_model.max_output_tokens,
            "timeout_seconds": evaluation_model.timeout_seconds,
            "cost_guard": (
                "exact_free_slug_zero_price_ceiling_no_provider_fallback"
                if evaluation_model.provider == "openrouter"
                else "requires_confirmed_groq_free_account_tier"
            ),
        }
    evaluation_settings = settings.model_copy(
        update={"generation_models": evaluation_models},
    )
    return metadata, FreeTierEvaluationGenerationService(evaluation_settings)


def generation_summary(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate technical completion and latency without semantic grading."""

    summary = {}
    for model_id in CANDIDATE_MODEL_IDS:
        model_rows = [row for row in outcomes if row["model_id"] == model_id]
        summary[model_id] = {}
        for language in ("all", "en", "sw", "mixed"):
            rows = model_rows if language == "all" else [
                row for row in model_rows if row["language"] == language
            ]
            generated = [row for row in rows if row.get("generation_called")]
            successful = [row for row in rows if row["status"] == "completed"]
            failures = [row for row in rows if row["status"] == "failed"]
            quotas = [
                row for row in rows
                if row["status"] == "quota_interrupted"
            ]
            latencies = [
                row["generation_ms"] for row in generated
                if row.get("generation_ms") is not None
            ]
            attempts = sum(row.get("provider_attempt_count", 0) for row in rows)
            summary[model_id][language] = {
                "attempted_cases": len(rows),
                "completed_cases": len(successful),
                "actual_llm_generated_responses": len(generated),
                "deterministic_backend_responses": len(rows) - len(generated),
                "final_technical_failures": len(failures),
                "quota_interruptions": len(quotas),
                "failure_rate": len(failures) / len(rows) if rows else None,
                "provider_request_attempts": attempts,
                "api_request_failure_rate": (
                    sum(max(0, row.get("provider_attempt_count", 0) - 1) for row in rows)
                    / attempts if attempts else None
                ),
                "generation_latency_ms": {
                    "mean": statistics.fmean(latencies) if latencies else None,
                    "median": statistics.median(latencies) if latencies else None,
                    "minimum": min(latencies) if latencies else None,
                    "maximum": max(latencies) if latencies else None,
                    "p95": percentile(latencies, 0.95),
                    "standard_deviation": (
                        statistics.pstdev(latencies) if len(latencies) > 1 else 0.0
                    ),
                },
                "semantic_quality_status": "awaiting_researcher_assessment",
            }
    return summary


def save_generation_report(
    outcomes: list[dict[str, Any]],
    model_metadata: dict[str, Any],
) -> None:
    """Atomically persist resumable raw results and technical aggregates."""

    write_json_atomic(RESULTS_PATH, {
        "schema_version": "kq-final-three-model-evaluation-v1",
        "run_id": "kq-rag-eval-200-three-model-comparison",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_partition": "held_out",
        "candidate_models": model_metadata,
        "external_judges_used": False,
        "quality_metrics_status": "awaiting_researcher_assessment",
        "outcomes": outcomes,
        "technical_aggregates": generation_summary(outcomes),
    })


def export_csv(outcomes: list[dict[str, Any]]) -> None:
    """Export raw results with model aliases and no private identity map."""

    if IDENTITY_PATH.exists():
        aliases = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    else:
        order = list(CANDIDATE_MODEL_IDS)
        secrets.SystemRandom().shuffle(order)
        aliases = {model: f"Model {chr(65 + index)}" for index, model in enumerate(order)}
        write_json_atomic(IDENTITY_PATH, aliases)
    fields = [
        "case_id", "anonymous_model", "language", "semantic_group", "question",
        "status", "decision", "generation_called", "generated_response",
        "generation_ms", "response_time_ms", "provider_attempt_count",
        "failure_type", "failure",
    ]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in outcomes:
            writer.writerow({
                "case_id": row["case_id"],
                "anonymous_model": aliases[row["model_id"]],
                "language": row["language"],
                "semantic_group": row["semantic_group"],
                "question": row["question"],
                "status": row["status"],
                "decision": row.get("decision"),
                "generation_called": row.get("generation_called"),
                "generated_response": row.get("final_response"),
                "generation_ms": row.get("generation_ms"),
                "response_time_ms": row.get("response_time_ms"),
                "provider_attempt_count": row.get("provider_attempt_count"),
                "failure_type": row.get("failure_type"),
                "failure": row.get("failure"),
            })


def run_generation(
    retrieval_report: dict,
    dataset: dict,
    *,
    authorised: bool,
    delay_seconds: float,
    model_ids: tuple[str, ...] = CANDIDATE_MODEL_IDS,
) -> None:
    """Interleave candidates, checkpointing every completed or failed attempt."""

    if not authorised:
        print("Retrieval completed; provider calls were not authorised.")
        return
    model_metadata, generation = configured_models()
    previous = []
    if RESULTS_PATH.exists():
        previous = json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get(
            "outcomes", [],
        )
    completed = {
        (row["case_id"], row["model_id"]): row
        for row in previous
        if row.get("status") == "completed"
    }
    # Retain quota and genuine failure records for auditability. A resumed
    # attempt replaces only its own case/model row after it is retried.
    outcomes = list(previous)
    dataset_by_id = {case["id"]: case for case in dataset["cases"]}
    total = len(retrieval_report["cases"]) * len(CANDIDATE_MODEL_IDS)
    quota_exhausted_models = set()
    # Finish deterministic no-call outcomes before spending or probing any quota.
    # This also makes those outcomes available when every provider is exhausted.
    ordered_retrieval_cases = sorted(
        retrieval_report["cases"],
        key=lambda item: item["generation_input"]["prompt"] is not None,
    )
    for retrieval_case in ordered_retrieval_cases:
        case = {
            **dataset_by_id[retrieval_case["case_id"]],
            "case_id": retrieval_case["case_id"],
        }
        for model_id in model_ids:
            key = (case["case_id"], model_id)
            if key in completed or model_id in quota_exhausted_models:
                continue
            started = perf_counter()
            try:
                prompt = retrieval_case["generation_input"]["prompt"]
                if prompt:
                    response = generation.generate(
                        prompt,
                        model_id=model_id,
                        system_instruction=SYSTEM_INSTRUCTION,
                    )
                    answer = response.text
                    generation_called = True
                    generation_ms = round((perf_counter() - started) * 1000, 3)
                    provider_duration_ms = response.provider_duration_ms
                    provider_attempt_count = response.attempt_count
                else:
                    answer = retrieval_case["context"]["message"]
                    generation_called = False
                    generation_ms = None
                    provider_duration_ms = None
                    provider_attempt_count = 0
                execution_ms = round((perf_counter() - started) * 1000, 3)
                outcome = {
                    "case_id": case["case_id"],
                    "semantic_group": case["semantic_group"],
                    "model_id": model_id,
                    "language": case["language"],
                    "category": case["category"],
                    "type": case["type"],
                    "question": case["question"],
                    "fixed_history": case.get("fixed_history"),
                    "status": "completed",
                    "decision": retrieval_case["context"]["decision"],
                    "generation_called": generation_called,
                    "final_response": answer,
                    "generation_ms": generation_ms,
                    "provider_duration_ms": provider_duration_ms,
                    "provider_attempt_count": provider_attempt_count,
                    "response_time_ms": round(
                        retrieval_case["retrieval"]["retrieval_ms"]
                        + retrieval_case["context"]["context_build_ms"]
                        + (generation_ms or 0.0),
                        3,
                    ),
                    "measured_replay_execution_ms": execution_ms,
                    "citations": sorted(set(re.findall(r"\[S\d+\]", answer or ""))),
                    "failure_type": None,
                    "failure": None,
                }
            except GenerationProviderError as error:
                quota_interruption = error.status_code == 429
                interruption_status = (
                    "free_provider_capacity_interrupted"
                    if quota_interruption
                    and model_metadata[model_id]["provider"] == "openrouter"
                    else "quota_interrupted"
                )
                outcome = {
                    "case_id": case["case_id"],
                    "semantic_group": case["semantic_group"],
                    "model_id": model_id,
                    "language": case["language"],
                    "category": case["category"],
                    "type": case["type"],
                    "question": case["question"],
                    "fixed_history": case.get("fixed_history"),
                    "status": (
                        interruption_status if quota_interruption else "failed"
                    ),
                    "decision": None,
                    "generation_called": True,
                    "final_response": None,
                    "generation_ms": None,
                    "provider_duration_ms": None,
                    "provider_attempt_count": getattr(error, "attempt_count", 1),
                    "response_time_ms": None,
                    "measured_replay_execution_ms": round(
                        (perf_counter() - started) * 1000, 3,
                    ),
                    "citations": [],
                    "failure_type": f"provider_{error.status_code}",
                    "failure": error.detail,
                }
                if quota_interruption:
                    # One recorded interruption is sufficient. Remaining cases for
                    # this model stay absent and are retried on the next resume.
                    quota_exhausted_models.add(model_id)
            outcomes = [row for row in outcomes if (
                row["case_id"], row["model_id"]
            ) != key]
            outcomes.append(outcome)
            save_generation_report(outcomes, model_metadata)
            export_csv(outcomes)
            print(
                f"[generation {len(outcomes)}/{total}] "
                f"{case['case_id']} / {model_id}: {outcome['status']}",
                flush=True,
            )
            if delay_seconds > 0 and outcome.get("generation_called"):
                sleep(delay_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorise-external-calls", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--force-retrieval", action="store_true")
    parser.add_argument(
        "--model-id",
        action="append",
        choices=CANDIDATE_MODEL_IDS,
        help="Resume only the named candidate; may be supplied more than once.",
    )
    args = parser.parse_args()
    dataset = load_dataset()
    if RETRIEVAL_PATH.exists() and not args.force_retrieval:
        retrieval = json.loads(RETRIEVAL_PATH.read_text(encoding="utf-8"))
        print("Reusing completed retrieval results; retrieval was not repeated.")
    else:
        retrieval, _internal, _prepared, _settings = run_retrieval(dataset)
    if not args.retrieval_only:
        run_generation(
            retrieval,
            dataset,
            authorised=args.authorise_external_calls,
            delay_seconds=args.delay_seconds,
            model_ids=tuple(args.model_id or CANDIDATE_MODEL_IDS),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
