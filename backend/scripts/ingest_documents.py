import json
from pathlib import Path
import sys


# Running "python scripts/ingest_documents.py" makes Python start inside scripts/.
# Adding the backend root lets the script import the app package consistently.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from app.config import get_settings
from app.rag.chunking import chunk_documents
from app.rag.document_loader import load_knowledge_base_documents


OUTPUT_FILE_NAME = "kq_document_chunks.json"


def main() -> None:
    """Load raw KQ documents, split them into chunks, and save JSON for review."""

    settings = get_settings()

    # The raw folder contains the original Kenya Airways .docx knowledge-base files.
    documents = load_knowledge_base_documents(settings.raw_data_dir)

    # Chunking creates the smaller text units that will be embedded in the next increment.
    chunks = chunk_documents(
        documents=documents,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )

    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.processed_data_dir / OUTPUT_FILE_NAME

    payload = {
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "chunk_size": settings.rag_chunk_size,
        "chunk_overlap": settings.rag_chunk_overlap,
        "chunks": [chunk.model_dump() for chunk in chunks],
    }

    # ensure_ascii=False preserves Kiswahili and other non-English text correctly.
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Processed {len(documents)} documents into {len(chunks)} chunks.")
    print(f"Saved chunks to: {output_path}")


if __name__ == "__main__":
    main()
