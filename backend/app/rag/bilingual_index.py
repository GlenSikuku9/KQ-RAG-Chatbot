"""Build bilingual search text while preserving authoritative English parents."""

from pathlib import Path

from app.models.document import KnowledgeBaseDocument
from app.models.document_chunk import DocumentChunk
from app.rag.chunking import chunk_documents
from app.rag.document_loader import load_knowledge_base_document


KISWAHILI_SUFFIX = " - sw"


def _document_name(source: str) -> str:
    """Return a platform-independent filename for a stored source path."""

    return Path(source.replace("\\", "/")).name


def _source_path(raw_data_dir: Path, source: str) -> Path:
    """Resolve a stored relative source without accepting path traversal."""

    relative = Path(*source.replace("\\", "/").split("/"))
    raw_root = raw_data_dir.resolve()
    resolved = (raw_root / relative).resolve()
    if raw_root not in resolved.parents:
        raise ValueError("Knowledge-base source is outside the raw-data directory.")
    return resolved


def _english_name_for_translation(source: str) -> str | None:
    """Resolve ``Policy - SW.docx`` to its authoritative English filename."""

    name = _document_name(source)
    path = Path(name)
    if not path.stem.casefold().endswith(KISWAHILI_SUFFIX):
        return None

    english_stem = path.stem[: -len(KISWAHILI_SUFFIX)]
    return f"{english_stem}{path.suffix}".casefold()


def _translated_block_map(document: KnowledgeBaseDocument) -> dict[str, str]:
    """Map stable DOCX structural locations to their Kiswahili text."""

    values: dict[str, str] = {}
    for block in document.blocks:
        values[block.source_location] = block.text
        for row in block.rows:
            values[
                f"{block.source_location}/row {row.row_index}"
            ] = row.text
    return values


def _translated_parent_text(
    parent: DocumentChunk,
    locations: dict[str, str],
) -> str:
    """Join translated blocks corresponding to one authoritative parent."""

    parts: list[str] = []
    for location in parent.source_locations:
        value = locations.get(location)
        if value and value not in parts:
            parts.append(value)

    if not parts:
        raise ValueError(
            "No Kiswahili structural text corresponds to English parent "
            f"{parent.chunk_id}.",
        )
    return "\n\n".join(parts)


def build_bilingual_search_snapshot(
    chunks: list[DocumentChunk],
    raw_data_dir: Path,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[list[DocumentChunk], dict[str, list[str]]]:
    """Return English parents with English/Kiswahili search representations.

    Active ``- SW`` documents are search-only. Their structural text is mapped
    onto the corresponding English parent, so equivalent languages cannot take
    multiple final ranks and only English policy evidence reaches generation.
    English documents without a published translation remain searchable using
    their original text, preserving the existing document-upload workflow.
    """

    # Firestore determines which document versions are published. Re-extracting
    # those exact versions applies E20's chunk boundaries consistently without
    # requiring every stored manifest to be rewritten in the same deployment.
    active_versions: dict[str, str] = {}
    for chunk in chunks:
        existing_version = active_versions.setdefault(
            chunk.source,
            chunk.version,
        )
        if existing_version != chunk.version:
            raise ValueError(
                f"Active source {chunk.source} contains multiple versions.",
            )

    english_documents: dict[str, KnowledgeBaseDocument] = {}
    translated_locations: dict[str, dict[str, str]] = {}
    for source, active_version in sorted(active_versions.items()):
        document = load_knowledge_base_document(
            _source_path(raw_data_dir, source),
            raw_data_dir,
        )
        if document.version != active_version:
            raise ValueError(
                f"Raw source {source} differs from its published version.",
            )

        english_name = _english_name_for_translation(source)
        if english_name is None:
            name = _document_name(source).casefold()
            if name in english_documents:
                raise ValueError(
                    f"Multiple active English documents share filename {name}.",
                )
            english_documents[name] = document
            continue

        if english_name in translated_locations:
            raise ValueError(
                "Multiple active Kiswahili documents map to "
                f"{english_name}.",
            )
        translated_locations[english_name] = _translated_block_map(document)

    unmatched = set(translated_locations) - set(english_documents)
    if unmatched:
        raise ValueError(
            "Active Kiswahili documents have no authoritative English parent: "
            + ", ".join(sorted(unmatched)),
        )

    parents = chunk_documents(
        list(english_documents.values()),
        chunk_size,
        chunk_overlap,
    )

    representations: dict[str, list[str]] = {}
    for parent in parents:
        english_name = _document_name(parent.source).casefold()
        search_texts = [parent.text]
        if english_name in translated_locations:
            search_texts.append(
                _translated_parent_text(
                    parent,
                    translated_locations[english_name],
                ),
            )
        representations[parent.chunk_id] = search_texts

    parents.sort(key=lambda parent: parent.chunk_id)
    return parents, representations
