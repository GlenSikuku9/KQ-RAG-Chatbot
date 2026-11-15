"""Defensive validation for administrator-supplied DOCX packages."""

from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile


class UnsafeDocumentError(ValueError):
    """The upload is not a safe, bounded OOXML Word document."""


_REQUIRED_PARTS = {
    "[Content_Types].xml",
    "word/document.xml",
}
_FORBIDDEN_SUFFIXES = {
    ".bin",
    ".dll",
    ".exe",
    ".js",
    ".msi",
    ".ps1",
    ".sh",
}


def validate_docx_upload(
    payload: bytes,
    *,
    max_bytes: int,
    max_uncompressed_bytes: int,
    max_entries: int = 2000,
) -> None:
    """Validate the OOXML container without extracting anything to disk."""

    if not payload:
        raise UnsafeDocumentError("The uploaded document is empty.")
    if len(payload) > max_bytes:
        raise UnsafeDocumentError("The DOCX file exceeds the upload size limit.")
    if not payload.startswith(b"PK"):
        raise UnsafeDocumentError("The upload is not a valid DOCX package.")

    try:
        with ZipFile(BytesIO(payload)) as package:
            entries = package.infolist()
            if len(entries) > max_entries:
                raise UnsafeDocumentError(
                    "The DOCX package contains too many archive entries.",
                )

            names: set[str] = set()
            uncompressed_total = 0
            for entry in entries:
                normalized = entry.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or normalized.startswith("/")
                ):
                    raise UnsafeDocumentError(
                        "The DOCX package contains an unsafe archive path.",
                    )
                if entry.flag_bits & 0x1:
                    raise UnsafeDocumentError(
                        "Encrypted DOCX archive entries are not supported.",
                    )
                if path.suffix.lower() in _FORBIDDEN_SUFFIXES:
                    raise UnsafeDocumentError(
                        "The DOCX package contains executable embedded content.",
                    )
                names.add(normalized)
                uncompressed_total += entry.file_size
                if uncompressed_total > max_uncompressed_bytes:
                    raise UnsafeDocumentError(
                        "The expanded DOCX package exceeds the processing limit.",
                    )

            if not _REQUIRED_PARTS.issubset(names):
                raise UnsafeDocumentError(
                    "The upload does not contain the required DOCX parts.",
                )
            if package.testzip() is not None:
                raise UnsafeDocumentError(
                    "The DOCX package contains a corrupt archive entry.",
                )
    except BadZipFile as error:
        raise UnsafeDocumentError(
            "The upload is not a readable DOCX package.",
        ) from error
