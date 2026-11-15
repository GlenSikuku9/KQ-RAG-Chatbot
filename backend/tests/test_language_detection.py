"""Focused tests for local passenger-message language detection."""

import unittest

from app.services.language_detection import LanguageDetector


class LanguageDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.detector = LanguageDetector()

    def test_detects_supported_passenger_languages(self) -> None:
        cases = (
            ("What is the baggage allowance?", "en"),
            ("Je, ninaweza kubadilisha tarehe ya safari yangu?", "sw"),
            ("Nataka kujua baggage allowance ya economy class", "mixed"),
            ("Can I change tarehe ya safari yangu?", "mixed"),
            ("Hello", "en"),
            ("Habari", "sw"),
            ("Hello habari", "mixed"),
            ("Ndege KQ100 imechelewa leo?", "sw"),
            ("Flight KQ100 imechelewa?", "mixed"),
            ("Nipe status ya flight KQ310 leo.", "mixed"),
        )

        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    self.detector.detect(message).language,
                    expected,
                )

    def test_ambiguous_short_turn_uses_conversation_language(self) -> None:
        result = self.detector.detect(
            "Okay",
            previous_language="sw",
        )

        self.assertEqual(result.language, "sw")
        self.assertEqual(result.source, "conversation_context")

    def test_ambiguous_first_turn_remains_explicitly_unknown(self) -> None:
        result = self.detector.detect("Okay")

        self.assertEqual(result.language, "unknown")
        self.assertEqual(result.source, "undetermined")


if __name__ == "__main__":
    unittest.main()
