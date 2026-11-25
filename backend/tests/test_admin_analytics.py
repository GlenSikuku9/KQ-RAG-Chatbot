"""Focused tests for administrator analytics aggregation."""

from types import SimpleNamespace

from app.services.admin import _analytics_language_counts


def test_language_distribution_excludes_unknown_records():
    conversations = [
        SimpleNamespace(language="en"),
        SimpleNamespace(language="sw"),
        SimpleNamespace(language="mixed"),
        SimpleNamespace(language="unknown"),
    ]

    counts = _analytics_language_counts(conversations)

    assert counts == {"en": 1, "sw": 1, "mixed": 1}
