"""Run official RAGAS metrics against an explicit local model report.

This command may call the configured evaluator provider. It is never imported
by passenger request handling and never runs on application startup.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.services.firestore_client import firestore_operation, get_firestore_client
from app.services.ragas_evaluator import build_ragas_scorers, evaluate_report


def _persist(report: dict) -> None:
    """Persist scores and provenance, never full questions or policy context."""

    client = get_firestore_client()
    evaluated_at = datetime.fromisoformat(report["evaluated_at"])
    records = []
    for index, result in enumerate(report["results"]):
        record = {
            key: value
            for key, value in result.items()
            if key not in {"reason", "error"}
        }
        record.update({
            "record_type": "case",
            "run_id": report["run_id"],
            "evaluated_at": evaluated_at,
            "ragas_version": report["ragas_version"],
            "evaluator_model": report["evaluator_model"],
            "embedding_method": report["embedding_method"],
            "evaluation_method": report["evaluation_method"],
        })
        records.append((f"{report['run_id']}-case-{index}", record))
    for index, aggregate in enumerate(report["aggregates"]):
        record = {
            **aggregate,
            "evaluated_at": datetime.fromisoformat(aggregate["evaluated_at"]),
        }
        records.append((f"{report['run_id']}-aggregate-{index}", record))

    for document_id, record in records:
        with firestore_operation():
            client.collection("ragas_evaluations").document(document_id).set(
                record,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run explicit RAGAS 0.4 evaluation on a model report.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse successful judgments from the output/checkpoint files.",
    )
    args = parser.parse_args(argv)

    source_report = json.loads(args.input.read_text(encoding="utf-8"))
    scorers, metadata = build_ragas_scorers(get_settings())
    checkpoint_path = args.output.with_suffix(".checkpoint.json")
    prior_results = []
    if args.resume:
        for path in (args.output, checkpoint_path):
            if path.exists():
                previous = json.loads(path.read_text(encoding="utf-8"))
                prior_results.extend(previous.get("results", []))

    def show_progress(result, completed, total, results):
        status = result["status"]
        identity = result["case_id"]
        if result.get("model_id"):
            identity += f" / {result['model_id']}"
        print(
            f"[{completed}/{total}] {result['metric_name']}: "
            f"{identity} - {status}",
            flush=True,
        )
        # A separate checkpoint keeps the final output valid and allows an
        # interrupted or disconnected run to continue without repeating work.
        checkpoint_path.write_text(
            json.dumps(
                {
                    "source_run_id": source_report["run_id"],
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    report = asyncio.run(
        evaluate_report(
            source_report,
            scorers,
            metadata,
            prior_results=prior_results,
            progress_callback=show_progress,
        ),
    )
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    failures = report.get("failure_summary", [])
    if failures:
        print(
            "RAGAS evaluation is incomplete. No results were persisted.",
            file=sys.stderr,
        )
        for failure in failures:
            print(
                f"- {failure['count']} x {failure['error_type']}: "
                f"{failure['error']}",
                file=sys.stderr,
            )
        return 2
    if not report["aggregates"]:
        print(
            "RAGAS produced no successful scores. No results were persisted.",
            file=sys.stderr,
        )
        return 2
    if args.persist:
        _persist(report)
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    print(json.dumps(report["aggregates"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
