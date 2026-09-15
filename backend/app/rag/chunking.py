import re

from app.models.document import KnowledgeBaseDocument
from app.models.document_chunk import DocumentChunk


def _normalize_text(text: str) -> str:
    """Make whitespace consistent so chunks are easier to embed and compare."""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_with_overlap(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split text into character-sized chunks while keeping useful context overlap."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap cannot be negative.")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size.")

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Prefer ending at a sentence or paragraph boundary instead of cutting mid-sentence.
        if end < len(text):
            sentence_end = max(text.rfind(". ", start, end), text.rfind("\n\n", start, end))
            if sentence_end > start:
                end = sentence_end + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        # Overlap helps the retriever keep context that crosses a chunk boundary.
        next_start = _move_back_to_word_boundary(text, max(end - chunk_overlap, 0))

        # If boundary adjustment moves us back to the same place, advance anyway.
        # This prevents an infinite loop on long words, table text, or unusual spacing.
        if next_start <= start:
            next_start = end

        start = next_start

    return chunks


def _move_back_to_word_boundary(text: str, position: int) -> int:
    """Move a chunk start backward so the next chunk does not begin mid-word."""

    while position > 0 and not text[position - 1].isspace():
        position -= 1
    return position


def chunk_documents(
    documents: list[KnowledgeBaseDocument],
    chunk_size: int,
    chunk_overlap: int,
) -> list[DocumentChunk]:
    """Convert full documents into smaller chunks ready for vector storage."""

    all_chunks: list[DocumentChunk] = []

    for document in documents:
        clean_text = _normalize_text(document.text)
        if not clean_text:
            continue

        text_chunks = _split_with_overlap(clean_text, chunk_size, chunk_overlap)

        for index, text in enumerate(text_chunks):
            # The ID is deterministic so repeated ingestion produces the same chunk IDs.
            chunk_id = f"{document.source}:{index}"
            all_chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    source=document.source,
                    file_type=document.file_type,
                    category=document.category,
                    chunk_index=index,
                    text=text,
                )
            )

    return all_chunks
