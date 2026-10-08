import argparse
import logging
from pathlib import Path
import sys


# Running "python scripts/ingest_documents.py" makes Python start inside scripts/.
# Adding the backend root lets the script import the app package consistently.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from app.config import get_settings
from fastapi import HTTPException
from app.rag.document_loader import discover_documents
from app.rag.ingestion import (
    OUTPUT_FILE_NAME, REPORT_FILE_NAME, preview_documents, publication_report,
    publish_document, remove_document, write_json_atomic,
)
from app.services.firestore_client import get_firestore_client
from app.services.knowledge_base import KnowledgeBaseStore


logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview DOCX ingestion or explicitly publish to Firestore.")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--publish", action="store_true", help="Publish discovered documents; never remove missing files.")
    operations.add_argument("--remove", metavar="SOURCE", help="Remove one relative DOCX source from Firestore, not disk.")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        if args.publish or args.remove is not None:
            files = discover_documents(settings.raw_data_dir) if args.publish else []
            store = KnowledgeBaseStore(get_firestore_client())
            results = (
                [publish_document(path, settings, store) for path in files] if args.publish
                else [remove_document(args.remove, settings, store)]
            )
            payload = publication_report(results)
            for result in results:
                print(f"{result.source}: {result.status}; persisted={result.persisted}; active chunks={result.active_chunk_count}")
                if result.error:
                    print(result.error, file=sys.stderr)
            output_path = settings.processed_data_dir / REPORT_FILE_NAME
        else:
            payload = preview_documents(settings)
            output_path = settings.processed_data_dir / OUTPUT_FILE_NAME
            print(f"Previewed {payload['document_count']} documents into {payload['chunk_count']} chunks; no database writes.")
        write_json_atomic(output_path, payload)
        print(f"Failed: {payload['failed_count']}; review required: {payload['review_required_count']}.")
        print(f"Saved report to: {output_path}")
        return 1 if payload["failed_count"] or payload["review_required_count"] else 0
    except HTTPException as exc:
        logger.error("Ingestion storage unavailable (HTTP %s).", exc.status_code)
        print(exc.detail, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        logger.error("Ingestion command failed (%s).", type(exc).__name__)
        print(f"Ingestion failed ({type(exc).__name__}); check the input paths, configuration, and output permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
