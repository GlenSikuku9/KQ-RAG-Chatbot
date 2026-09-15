from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.models.document import KnowledgeBaseDocument

SUPPORTED_EXTENSIONS = {".docx", ".pdf"}


def _category_from_file_name(file_path: Path) -> str:
    """Turn a file name into a readable category for citations and filtering."""

    return file_path.stem.replace("-", " ").replace("_", " ").strip()


def _iter_docx_blocks(document: Document) -> list[Paragraph | Table]:
    """Return top-level Word paragraphs and tables in their original document order."""

    blocks: list[Paragraph | Table] = []
    body = document.element.body

    for child in body.iterchildren():
        # Word stores paragraphs and tables as different XML tags under the document body.
        if child.tag.endswith("}p"):
            blocks.append(Paragraph(child, document))
        elif child.tag.endswith("}tbl"):
            blocks.append(Table(child, document))

    return blocks


def _extract_table_text(table: Table) -> str:
    """Convert a Word table into readable text so table facts are not lost."""

    rows: list[list[str]] = []

    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        cleaned_cells = [cell for cell in cells if cell]

        if cleaned_cells:
            rows.append(cleaned_cells)

    if not rows:
        return ""

    header = rows[0]
    body_rows = rows[1:]
    formatted_rows: list[str] = []

    for row in body_rows:
        # If the first row looks like headings, pair each heading with its cell value.
        if len(header) == len(row) and len(header) > 1:
            formatted_cells = [
                f"{heading}: {value}"
                for heading, value in zip(header, row, strict=True)
                if heading and value
            ]
            formatted_rows.append(" | ".join(formatted_cells))
        else:
            formatted_rows.append(" | ".join(row))

    if not formatted_rows:
        formatted_rows.append(" | ".join(header))

    return "\n".join(formatted_rows)


def _extract_docx_text(file_path: Path) -> str:
    """Read paragraphs and tables from a .docx file and return clean plain text."""

    document = Document(file_path)
    text_blocks: list[str] = []

    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            paragraph_text = block.text.strip()
            if paragraph_text:
                text_blocks.append(paragraph_text)
        else:
            table_text = _extract_table_text(block)
            if table_text:
                # Label table text so retrieved chunks remain understandable to the chatbot.
                text_blocks.append(f"Table:\n{table_text}")

    return "\n\n".join(text_blocks)


def _extract_pdf_text(file_path: Path) -> str:
    """Read selectable text from a PDF file page by page."""

    reader = PdfReader(file_path)
    pages: list[str] = []

    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text()
        if page_text and page_text.strip():
            # Keep page numbers in the text so future citations can refer back to the PDF page.
            pages.append(f"Page {page_number}:\n{page_text.strip()}")

    return "\n\n".join(pages)


def _extract_text(file_path: Path) -> str:
    """Choose the correct extractor based on the source file extension."""

    extension = file_path.suffix.lower()

    if extension == ".docx":
        return _extract_docx_text(file_path)
    if extension == ".pdf":
        return _extract_pdf_text(file_path)

    raise ValueError(f"Unsupported knowledge-base file type: {file_path.suffix}")


def load_knowledge_base_documents(raw_data_dir: Path) -> list[KnowledgeBaseDocument]:
    """Load supported knowledge-base files from the configured raw data folder."""

    if not raw_data_dir.exists():
        raise FileNotFoundError(f"Raw data directory does not exist: {raw_data_dir}")

    source_files = sorted(
        file_path
        for file_path in raw_data_dir.rglob("*")
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not source_files:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise FileNotFoundError(
            f"No supported knowledge-base files ({supported}) were found in: {raw_data_dir}"
        )

    documents: list[KnowledgeBaseDocument] = []

    for file_path in source_files:
        text = _extract_text(file_path)

        # Keep source metadata beside the text so generated answers can cite files later.
        documents.append(
            KnowledgeBaseDocument(
                source=file_path.name,
                file_type=file_path.suffix.lower().lstrip("."),
                category=_category_from_file_name(file_path),
                text=text,
            )
        )

    return documents
