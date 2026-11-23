from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.models.document import DocumentBlock, KnowledgeBaseDocument
from app.models.document_chunk import DocumentChunk
from app.rag.bilingual_index import build_bilingual_search_snapshot


def chunk(
    identifier: str,
    document: KnowledgeBaseDocument,
) -> DocumentChunk:
    """Create an active-manifest marker for one published document."""

    return DocumentChunk(
        chunk_id=identifier,
        document_id=f"document-{identifier}",
        version=document.version,
        source=document.source,
        file_type="docx",
        category=document.category,
        chunk_index=0,
        text=document.text,
        source_locations=["Body/block 1"],
    )


def document(
    source: str,
    text: str,
) -> KnowledgeBaseDocument:
    """Create one structurally traceable source document."""

    return KnowledgeBaseDocument(
        source=source,
        file_type="docx",
        category="Policy",
        text=text,
        blocks=[
            DocumentBlock(
                kind="paragraph",
                text=text,
                source_location="Body/block 1",
            ),
        ],
    )


class BilingualIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.raw_data_dir = Path(temporary.name)

    def test_translation_maps_to_authoritative_english_parent(self):
        english_document = document(
            "KQ Data/Policy.docx",
            "Baggage policy",
        )
        translated_document = document(
            "admin/Policy - SW.docx",
            "Sera ya mizigo",
        )
        english = chunk("english-active", english_document)
        kiswahili = chunk("kiswahili-active", translated_document)

        with patch(
            "app.rag.bilingual_index.load_knowledge_base_document",
            side_effect=[english_document, translated_document],
        ):
            parents, representations = build_bilingual_search_snapshot(
                [english, kiswahili],
                self.raw_data_dir,
                1800,
                360,
            )

        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0].source, english_document.source)
        self.assertEqual(parents[0].text, english_document.text)
        self.assertEqual(
            representations[parents[0].chunk_id],
            ["Baggage policy", "Sera ya mizigo"],
        )

    def test_english_document_without_translation_remains_searchable(self):
        english_document = document(
            "admin/New policy.docx",
            "New policy text",
        )
        english = chunk("english-active", english_document)

        with patch(
            "app.rag.bilingual_index.load_knowledge_base_document",
            return_value=english_document,
        ):
            parents, representations = build_bilingual_search_snapshot(
                [english],
                self.raw_data_dir,
                1800,
                360,
            )

        self.assertEqual(len(parents), 1)
        self.assertEqual(
            representations[parents[0].chunk_id],
            ["New policy text"],
        )

    def test_translation_requires_an_active_english_parent(self):
        translated_document = document(
            "admin/Policy - SW.docx",
            "Sera ya mizigo",
        )
        kiswahili = chunk("kiswahili-active", translated_document)

        with patch(
            "app.rag.bilingual_index.load_knowledge_base_document",
            return_value=translated_document,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "no authoritative English parent",
            ):
                build_bilingual_search_snapshot(
                    [kiswahili],
                    self.raw_data_dir,
                    1800,
                    360,
                )


if __name__ == "__main__":
    unittest.main()
