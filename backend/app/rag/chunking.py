from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import logging

from app.config import validate_chunk_settings
from app.models.document import DocumentBlock, KnowledgeBaseDocument, TableRow
from app.models.document_chunk import DocumentChunk


logger = logging.getLogger(__name__)
CHUNKING_VERSION = "adaptive-rows-v1"


@dataclass
class _ChunkContent:
    text: str
    section: str | None
    locations: list[str]
    table_location: str | None = None
    table_rows: list[int] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    """Make whitespace consistent so chunks are easier to embed and compare."""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_with_overlap(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split prose at natural boundaries; an unbroken token may exceed the target."""
    validate_chunk_settings(chunk_size, chunk_overlap)
    return [text[start:end].strip() for start, end in _split_spans(text, chunk_size, chunk_overlap)]


def _split_spans(text: str, chunk_size: int, chunk_overlap: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    previous_end = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start == len(text):
            break
        end = min(start + chunk_size, len(text))
        if end < len(text):
            minimum = max(start + chunk_size // 2, previous_end + 1)
            paragraph_end = text.rfind("\n\n", minimum, end + 1)
            sentences = list(re.finditer(r"[.!?](?=\s)", text[minimum:end]))
            if paragraph_end >= minimum:
                end = paragraph_end
            elif sentences:
                end = minimum + sentences[-1].end()
            elif not text[end].isspace() and not text[end - 1].isspace():
                boundary = _move_back_to_word_boundary(text, end)
                if boundary > start:
                    if boundary <= previous_end:
                        start = previous_end
                        continue
                    end = boundary
                else:
                    next_space = re.search(r"\s", text[end:])
                    end = end + next_space.start() if next_space else len(text)
        spans.append((start, end))
        if end == len(text):
            break
        next_start = max(end - chunk_overlap, start + 1)
        # Move forward, not backward, so overlap never exceeds the configured budget.
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        previous_end = end
        start = next_start
    return spans


def _move_back_to_word_boundary(text: str, position: int) -> int:
    """Move a chunk start backward so the next chunk does not begin mid-word."""

    while position > 0 and not text[position - 1].isspace():
        position -= 1
    return position


def _prose_chunks(blocks: list[DocumentBlock], chunk_size: int, chunk_overlap: int) -> list[_ChunkContent]:
    if not blocks:
        return []
    section = blocks[0].section
    prefix = f"Section: {section}\n\n" if section else ""
    ranges: list[tuple[int, int, str]] = []
    parts: list[str] = []
    heading_locations: list[str] = []
    offset = 0
    for block in blocks:
        if block.kind == "heading" and section:
            heading_locations.append(block.source_location)
            continue
        text = _normalize_text(block.text)
        parts.append(text)
        ranges.append((offset, offset + len(text), block.source_location))
        offset += len(text) + 2
    combined = "\n\n".join(parts)
    if not combined:
        return [_ChunkContent(prefix.strip(), section, heading_locations)]
    target = chunk_size - len(prefix) if len(prefix) < chunk_size else chunk_size
    overlap = min(chunk_overlap, target - 1)
    return [
        _ChunkContent(
            prefix + combined[start:end].strip(), section,
            heading_locations + [location for first, last, location in ranges if first < end and last > start],
        )
        for start, end in _split_spans(combined, target, overlap)
    ]


def _table_chunks(
    block: DocumentBlock, before: DocumentBlock | None, after: DocumentBlock | None,
    chunk_size: int, chunk_overlap: int,
) -> list[_ChunkContent]:
    context: list[str] = []
    locations: list[str] = []
    if block.section:
        context.append(f"Section: {block.section}")
    if before is not None:
        context.append("Preceding paragraph:\n" + _normalize_text(before.text))
        locations.append(before.source_location)
    context.append(f"Table ({block.source_location}):")
    trailing = ""
    if after is not None:
        trailing = "\n\nFollowing paragraph:\n" + _normalize_text(after.text)
        locations.append(after.source_location)

    def render(rows: list[TableRow], headers: list[TableRow]) -> _ChunkContent:
        text = "\n\n".join(context) + "\n" + "\n".join(_normalize_text(row.text) for row in rows) + trailing
        included = headers + rows
        return _ChunkContent(
            text, block.section,
            list(dict.fromkeys(locations + [f"{block.source_location}/row {row.row_index}" for row in included])),
            block.source_location, [row.row_index for row in included],
        )

    if block.keep_together or not block.rows:
        return [_ChunkContent(
            "\n\n".join(context[:-1]) + ("\n\n" if len(context) > 1 else "")
            + f"Table ({block.source_location}):\n" + _normalize_text(block.text) + trailing,
            block.section, locations + [block.source_location],
            block.source_location, [row.row_index for row in block.rows],
        )]

    headers = [row for row in block.rows if row.is_header]
    if headers:
        context.append("Declared header rows:\n" + "\n".join(_normalize_text(row.text) for row in headers))
    else:
        headers = block.rows[:1]
        context.append("Opening row (context, not a declared header):\n" + _normalize_text(headers[0].text))
    data = [row for row in block.rows if row not in headers]
    if not data:
        return [render([], headers)]

    chunks: list[_ChunkContent] = []
    group: list[TableRow] = []
    for row in data:
        if group and len(render(group + [row], headers).text) > chunk_size:
            chunks.append(render(group, headers))
            overlap_rows: list[TableRow] = []
            for previous in reversed(group):
                candidate = [previous] + overlap_rows
                if len("\n".join(item.text for item in candidate)) > chunk_overlap:
                    break
                if len(render(candidate + [row], headers).text) > chunk_size:
                    break
                overlap_rows = candidate
            group = overlap_rows
        group.append(row)
    if group:
        chunks.append(render(group, headers))
    return chunks


def _document_chunks(
    document: KnowledgeBaseDocument, chunk_size: int, chunk_overlap: int,
) -> list[_ChunkContent]:
    blocks = document.blocks or [
        DocumentBlock(kind="paragraph", text=document.text, source_location="Extracted text")
    ]
    chunks: list[_ChunkContent] = []
    prose: list[DocumentBlock] = []
    for index, block in enumerate(blocks):
        if prose and (block.kind == "table" or block.keep_together or block.section != prose[0].section):
            chunks.extend(_prose_chunks(prose, chunk_size, chunk_overlap))
            prose = []
        if block.kind == "table":
            before = blocks[index - 1] if index else None
            after = blocks[index + 1] if index + 1 < len(blocks) else None
            if before is not None and (before.kind != "paragraph" or before.section != block.section):
                before = None
            if after is not None and (after.kind != "paragraph" or after.section != block.section):
                after = None
            # A paragraph immediately introducing the next table stays with that table.
            if index + 2 < len(blocks) and blocks[index + 2].kind == "table":
                after = None
            chunks.extend(_table_chunks(block, before, after, chunk_size, chunk_overlap))
        elif block.keep_together:
            chunks.append(_ChunkContent(_normalize_text(block.text), block.section, [block.source_location]))
        else:
            prose.append(block)
    chunks.extend(_prose_chunks(prose, chunk_size, chunk_overlap))
    return chunks


def chunk_documents(
    documents: list[KnowledgeBaseDocument],
    chunk_size: int,
    chunk_overlap: int,
) -> list[DocumentChunk]:
    """Create traceable chunks; table rows and complex tables may exceed the size target."""

    validate_chunk_settings(chunk_size, chunk_overlap)
    all_chunks: list[DocumentChunk] = []
    seen_documents: set[str] = set()
    for document in documents:
        if document.document_id in seen_documents:
            logger.error("Duplicate source document in chunking batch: %s.", document.source)
            raise ValueError(f"Duplicate source document: {document.source}")
        seen_documents.add(document.document_id)
        if document.extraction_warnings:
            logger.warning("Withholding %s from chunking: extraction review required.", document.source)
            continue
        clean_text = _normalize_text(document.text)
        if not clean_text:
            logger.error("Cannot chunk empty document %s.", document.source)
            raise ValueError(f"Cannot chunk empty document: {document.source}")

        if document.blocks and _normalize_text("\n\n".join(block.text for block in document.blocks)) != clean_text:
            logger.error("Structured blocks do not match extracted text for %s.", document.source)
            raise ValueError(f"Structured blocks do not match extracted text: {document.source}")
        text_chunks = _document_chunks(document, chunk_size, chunk_overlap)
        version = document.version
        for index, content in enumerate(text_chunks):
            identity = json.dumps([
                document.document_id, version, CHUNKING_VERSION, chunk_size, chunk_overlap,
                index, content.text, content.locations,
            ], ensure_ascii=False, separators=(",", ":"))
            chunk_id = sha256(identity.encode("utf-8")).hexdigest()
            all_chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    version=version,
                    source=document.source,
                    file_type=document.file_type,
                    category=document.category,
                    chunk_index=index,
                    text=content.text,
                    section=content.section,
                    source_locations=content.locations,
                    table_location=content.table_location,
                    table_rows=content.table_rows,
                )
            )

    return all_chunks
