"""Register the existing Kiswahili DOCX artefacts without rebuilding retrieval.

This migration is intentionally metadata-only for translations that have not
already been uploaded. It copies each reviewed artefact into managed document
storage and creates a pending manifest with no active chunks. An administrator
may later choose Process in the dashboard to publish a document explicitly.
"""

from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
import shutil
from uuid import uuid4

from app.config import get_settings
from app.models.database import encode_document_id, utc_now
from app.models.document import document_id_for_source
from app.models.ingestion import DocumentRecord, ProcessingAttempt
from app.rag.ingestion import chunking_spec
from app.services.firestore_client import FIRESTORE_READ_TIMEOUT, get_firestore_client


UPLOADER_EMAIL = "janedoe@gmail.com"
TRANSLATION_SUFFIX = " - SW"


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _english_filename(translated_name: str) -> str:
    stem = Path(translated_name).stem
    if not stem.endswith(TRANSLATION_SUFFIX):
        raise ValueError(f"Unexpected translation filename: {translated_name}")
    return f"{stem.removesuffix(TRANSLATION_SUFFIX)}.docx"


def register(apply: bool) -> dict:
    settings = get_settings()
    translations_dir = settings.raw_data_dir.parent / "translations" / "kiswahili"
    translations = sorted(translations_dir.glob("*.docx"))
    if len(translations) != 14:
        raise RuntimeError(f"Expected 14 Kiswahili DOCX files; found {len(translations)}.")

    client = get_firestore_client()
    snapshots = list(client.collection("documents").stream(timeout=FIRESTORE_READ_TIMEOUT))
    records = {
        DocumentRecord.model_validate(snapshot.to_dict()).source.replace("\\", "/"):
        DocumentRecord.model_validate(snapshot.to_dict())
        for snapshot in snapshots
    }
    by_filename = {
        Path(record.source).name.casefold(): record
        for record in records.values()
    }
    missing_english = [
        _english_filename(path.name)
        for path in translations
        if _english_filename(path.name).casefold() not in by_filename
    ]
    if missing_english:
        raise RuntimeError(
            "Cannot link translations because English manifests are missing: "
            + ", ".join(missing_english)
        )

    now = utc_now()
    planned = []
    all_document_ids = set()
    for translation in translations:
        english = by_filename[_english_filename(translation.name).casefold()]
        source = f"admin/{translation.name}"
        translated_id = document_id_for_source(source)
        existing = records.get(source)
        destination = settings.raw_data_dir / source
        if destination.exists() and _hash(destination) != _hash(translation):
            raise RuntimeError(
                f"Managed file differs from the canonical translation: {translation.name}"
            )

        planned.append({
            "translation": translation,
            "destination": destination,
            "source": source,
            "document_id": translated_id,
            "existing": existing,
            "english": english,
        })
        all_document_ids.update((english.document_id, translated_id))

    if apply:
        for item in planned:
            destination = item["destination"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(item["translation"], destination)

            if item["existing"] is None:
                record = DocumentRecord(
                    document_id=item["document_id"],
                    source=item["source"],
                    created_at=now,
                    updated_at=now,
                    revision=uuid4().hex,
                    active=None,
                    last_attempt=ProcessingAttempt(
                        status="pending",
                        chunking=chunking_spec(settings),
                        completed_at=now,
                    ),
                )
                client.collection("documents").document(
                    encode_document_id(record.document_id)
                ).set(record.model_dump())

            english_metadata = client.collection("admin_document_metadata").document(
                item["english"].document_id
            )
            english_metadata.set({
                "uploaded_by": UPLOADER_EMAIL,
                "language": "en",
                "related_document_id": item["document_id"],
            }, merge=True)
            client.collection("admin_document_metadata").document(
                item["document_id"]
            ).set({
                "title": item["translation"].stem,
                "category": (
                    item["english"].active.category
                    if item["english"].active
                    else Path(item["english"].source).stem
                ),
                "uploaded_by": UPLOADER_EMAIL,
                "uploaded_at": now,
                "original_filename": item["translation"].name,
                "file_hash": _hash(item["translation"]),
                "version_count": 1,
                "language": "sw",
                "related_document_id": item["english"].document_id,
                "translation_status": "unofficial_ai_assisted",
            }, merge=True)

        # Version history should display the same uploader as the main table.
        version_snapshots = client.collection("document_versions").stream(
            timeout=FIRESTORE_READ_TIMEOUT
        )
        for snapshot in version_snapshots:
            raw = snapshot.to_dict() or {}
            if raw.get("document_id") in all_document_ids:
                snapshot.reference.set({"uploaded_by": UPLOADER_EMAIL}, merge=True)

    final_count = None
    uploader_count = None
    if apply:
        final_snapshots = list(
            client.collection("documents").stream(timeout=FIRESTORE_READ_TIMEOUT)
        )
        final_records = [
            DocumentRecord.model_validate(snapshot.to_dict())
            for snapshot in final_snapshots
        ]
        final_ids = {record.document_id for record in final_records}
        metadata_snapshots = list(
            client.collection("admin_document_metadata").stream(
                timeout=FIRESTORE_READ_TIMEOUT
            )
        )
        uploader_count = sum(
            snapshot.id in final_ids
            and (snapshot.to_dict() or {}).get("uploaded_by") == UPLOADER_EMAIL
            for snapshot in metadata_snapshots
        )
        final_count = len(final_records)
        if final_count != 28 or uploader_count != 28:
            raise RuntimeError(
                "Post-migration verification failed: "
                f"documents={final_count}, uploader_records={uploader_count}."
            )

    return {
        "apply": apply,
        "english_documents": 14,
        "kiswahili_documents": 14,
        "existing_kiswahili_records": sum(item["existing"] is not None for item in planned),
        "new_pending_records": sum(item["existing"] is None for item in planned),
        "final_document_count": final_count,
        "janedoe_uploader_count": uploader_count,
        "active_index_changed": False,
    }


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write idempotent document and metadata records. Without this flag, only validate.",
    )
    result = register(parser.parse_args().apply)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
