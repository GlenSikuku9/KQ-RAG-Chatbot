"""Harmless regression tests for confirmed application security boundaries."""

from io import BytesIO
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.conversation import MessageChunk
from app.security.document_upload import (
    UnsafeDocumentError,
    validate_docx_upload,
)
from app.security.middleware import (
    ExpensiveRequestRateLimitMiddleware,
    SecurityHeadersMiddleware,
)


def docx_package(
    extra_entries: dict[str, bytes] | None = None,
) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            b"<Types/>",
        )
        package.writestr(
            "word/document.xml",
            b"<w:document xmlns:w='test'/>",
        )
        for name, content in (extra_entries or {}).items():
            package.writestr(name, content)
    return stream.getvalue()


class DocumentUploadSecurityTests(unittest.TestCase):
    def test_valid_bounded_docx_is_accepted(self) -> None:
        payload = docx_package()

        validate_docx_upload(
            payload,
            max_bytes=10_000,
            max_uncompressed_bytes=10_000,
        )

    def test_disguised_non_zip_and_oversized_uploads_are_rejected(self) -> None:
        for payload, max_bytes in (
            (b"not a docx", 10_000),
            (docx_package(), 10),
        ):
            with self.subTest(max_bytes=max_bytes):
                with self.assertRaises(UnsafeDocumentError):
                    validate_docx_upload(
                        payload,
                        max_bytes=max_bytes,
                        max_uncompressed_bytes=10_000,
                    )

    def test_traversal_executable_and_expansion_are_rejected(self) -> None:
        cases = (
            docx_package({"../outside.xml": b"unsafe"}),
            docx_package({"word/embeddings/payload.exe": b"unsafe"}),
            docx_package({"word/large.xml": b"x" * 20_000}),
        )

        for payload in cases:
            with self.subTest(payload_size=len(payload)):
                with self.assertRaises(UnsafeDocumentError):
                    validate_docx_upload(
                        payload,
                        max_bytes=100_000,
                        max_uncompressed_bytes=10_000,
                    )


class SourceUrlSecurityTests(unittest.TestCase):
    def test_only_official_https_source_url_survives_validation(self) -> None:
        base = {
            "message_chunk_id": "trace",
            "message_id": "message",
            "chunk_id": "chunk",
            "citation_id": "S1",
            "similarity_score": 0.9,
            "retrieval_rank": 1,
        }

        official = MessageChunk(
            **base,
            source_url="https://www.kenya-airways.com/en-ke/plan/",
        )
        malicious = MessageChunk(
            **base,
            source_url="javascript:alert(1)",
        )

        self.assertIsNotNone(official.source_url)
        self.assertIsNone(malicious.source_url)


class HttpSecurityTests(unittest.TestCase):
    def test_security_headers_and_rate_limit_are_applied(self) -> None:
        app = FastAPI()
        app.add_middleware(
            ExpensiveRequestRateLimitMiddleware,
            requests_per_minute=1,
        )
        app.add_middleware(SecurityHeadersMiddleware)

        @app.post("/api/v1/guest/messages")
        def guest_message():
            return {"ok": True}

        client = TestClient(app)
        first = client.post("/api/v1/guest/messages")
        second = client.post("/api/v1/guest/messages")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["x-content-type-options"], "nosniff")
        self.assertEqual(first.headers["x-frame-options"], "DENY")
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["retry-after"], "60")


if __name__ == "__main__":
    unittest.main()
