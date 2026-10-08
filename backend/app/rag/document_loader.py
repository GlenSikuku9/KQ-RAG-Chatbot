import logging
from pathlib import Path
from zipfile import BadZipFile

from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError

from app.models.document import KnowledgeBaseDocument
from app.rag.docx_extraction import ExtractionResult, extract_docx


logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".docx"}


class DocumentExtractionError(ValueError):
    """A source document cannot produce usable editable text."""


def _category_from_file_name(file_path: Path) -> str:
    return file_path.stem.replace("-", " ").replace("_", " ").strip()


def _extract_document(file_path: Path) -> ExtractionResult:
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        logger.error("Unsupported knowledge-base file type: %s", file_path.suffix)
        raise ValueError("Only DOCX knowledge-base documents are supported.")
    try:
        result = extract_docx(file_path)
    except (OSError, BadZipFile, PackageNotFoundError, XMLSyntaxError, KeyError, ValueError) as exc:
        logger.error("Cannot extract DOCX %s (%s).", file_path.name, type(exc).__name__)
        raise DocumentExtractionError(
            f"Cannot read DOCX document {file_path.name}. Check that it is a valid, accessible Word document."
        ) from exc
    for warning in result.warnings:
        logger.warning("DOCX review required for %s: %s", file_path.name, warning)
    if not result.text.strip():
        logger.error("DOCX %s has no extractable editable text.", file_path.name)
        raise DocumentExtractionError(
            f"No editable text found in {file_path.name}. Empty or image-only documents require correction."
        )
    return result


def _extract_text(file_path: Path) -> str:
    """Return review text; the batch loader also preserves structured warnings."""

    return _extract_document(file_path).text


def load_knowledge_base_documents(raw_data_dir: Path) -> list[KnowledgeBaseDocument]:
    """Load DOCX review content; warned documents are withheld from chunking."""

    if not raw_data_dir.is_dir():
        logger.error("Knowledge-base directory is unavailable.")
        raise FileNotFoundError(f"Raw data directory does not exist: {raw_data_dir}")
    source_files: list[Path] = []
    for file_path in sorted(raw_data_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name.startswith("~$"):
            logger.warning("Skipping Word lock file %s.", file_path.name)
        elif file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            source_files.append(file_path)
        else:
            logger.warning("Skipping unsupported knowledge-base file %s; only DOCX is supported.", file_path.name)
    if not source_files:
        logger.error("No DOCX knowledge-base documents found.")
        raise FileNotFoundError(f"No supported knowledge-base files (.docx) were found in: {raw_data_dir}")

    documents: list[KnowledgeBaseDocument] = []
    for file_path in source_files:
        result = _extract_document(file_path)
        documents.append(
            KnowledgeBaseDocument(
                source=str(file_path.relative_to(raw_data_dir)),
                file_type="docx",
                category=_category_from_file_name(file_path),
                text=result.text,
                extraction_warnings=result.warnings,
            )
        )
    return documents
