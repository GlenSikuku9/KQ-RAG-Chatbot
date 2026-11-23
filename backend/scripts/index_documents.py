import argparse
import json
import logging
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from fastapi import HTTPException
from app.config import get_settings
from app.rag.bilingual_index import build_bilingual_search_snapshot
from app.rag.embeddings import get_embedder
from app.rag.vector_index import VectorIndex, representation_hash
from app.models.retrieval import RetrievalRequest
from app.rag.retrieval import get_retrieval_service
from app.services.firestore_client import get_firestore_client
from app.services.knowledge_base import KnowledgeBaseStore


logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index published Firestore chunks using local multilingual E5.")
    parser.add_argument("--rebuild", action="store_true", help="Explicitly replace all vectors after model/config changes.")
    parser.add_argument("--query", help="Search the persisted index without re-embedding documents.")
    parser.add_argument("--language", choices=["en", "sw", "mixed"], help="Explicit preference; omitted means unknown.")
    parser.add_argument("--top-k", type=int, help="Number of distinct chunks to retrieve (1-20).")
    args = parser.parse_args(argv)
    try:
        if args.query is not None:
            if args.rebuild:
                parser.error("--rebuild cannot be combined with --query")
            request = RetrievalRequest(question=args.query, language=args.language, top_k=args.top_k)
            response = get_retrieval_service().retrieve(request)
            # ASCII escaping keeps diagnostic JSON printable in Windows
            # terminals while json.loads restores the original Unicode text.
            print(json.dumps(
                response.model_dump(mode="json"),
                ensure_ascii=True,
                indent=2,
            ))
        else:
            if args.language is not None or args.top_k is not None:
                parser.error("--language and --top-k require --query")
            settings = get_settings()
            index = VectorIndex(settings.chroma_persist_dir, get_embedder(), settings.embedding_batch_size)
            # Only published active content is authoritative; raw files and preview exports are not.
            store = KnowledgeBaseStore(get_firestore_client())
            snapshot = store.read_index_snapshot()
            parents, representations = build_bilingual_search_snapshot(
                snapshot,
                settings.raw_data_dir,
                settings.rag_chunk_size,
                settings.rag_chunk_overlap,
            )
            digest = representation_hash(
                parents,
                representations,
            )
            result = index.sync(
                parents,
                rebuild=args.rebuild,
                search_representations=representations,
                still_current=lambda: representation_hash(
                    *build_bilingual_search_snapshot(
                        store.read_index_snapshot(),
                        settings.raw_data_dir,
                        settings.rag_chunk_size,
                        settings.rag_chunk_overlap,
                    ),
                ) == digest,
            )
            print(json.dumps(result, indent=2))
        return 0
    except HTTPException as exc:
        logger.error("Index/retrieval operation failed (HTTP %s).", exc.status_code)
        print(exc.detail, file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("Index/retrieval operation failed (%s). Check input, model configuration, and index state.", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
