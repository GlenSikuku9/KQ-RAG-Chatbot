"""Apply reviewed semantic evidence corrections to development data only."""

from __future__ import annotations

import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.rag.ingestion import write_json_atomic
from scripts.evaluation_annotation_corrections import corrected_parent_ids
from scripts.experimental_evaluation import validate_dataset


DATASET_PATH = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "retrieval-evaluation-development-reviewed.json"
)


def main() -> int:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    validate_dataset(dataset, "development")
    changes = []

    for case in dataset["cases"]:
        for criterion in case["mandatory_evidence_criteria"]:
            corrected, removed = corrected_parent_ids(
                criterion["criterion_id"],
                criterion["acceptable_parent_chunk_ids"],
            )
            if not corrected:
                raise ValueError(
                    f"Correction emptied {criterion['criterion_id']}.",
                )
            criterion["acceptable_parent_chunk_ids"] = corrected
            if removed:
                changes.append({
                    "case_id": case["case_id"],
                    "criterion_id": criterion["criterion_id"],
                    "removed_parent_chunk_ids": removed,
                })

    dataset.setdefault("annotation_corrections", {})[
        "semantic_applicability_review"
    ] = {
        "status": "applied",
        "changes": changes,
        "note": (
            "Removed keyword-matching parents that do not directly support "
            "the criterion. Questions and reference answers are unchanged."
        ),
    }
    write_json_atomic(DATASET_PATH, dataset)
    print(f"Applied {len(changes)} case-level corrections to {DATASET_PATH}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
