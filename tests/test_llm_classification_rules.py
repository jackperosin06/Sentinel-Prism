"""Unit tests for classification rules (Story 3.4)."""

from __future__ import annotations

import uuid

from sentinel_prism.services.llm.rules import evaluate_classification_rules


def test_rules_in_scope_typical_update() -> None:
    out = evaluate_classification_rules(
        {
            "source_id": str(uuid.uuid4()),
            "item_url": "https://x/y",
            "jurisdiction": "EU",
            "document_type": "unknown",
            "title": "Safety notice",
            "summary": None,
            "body_snippet": None,
        }
    )
    assert out.in_scope is True
    assert out.reasons == ()


def test_rules_out_of_scope_jurisdiction() -> None:
    out = evaluate_classification_rules(
        {
            "jurisdiction": "ZZ-XX",
            "document_type": "unknown",
            "title": "T",
        }
    )
    assert out.in_scope is False
    assert "jurisdiction_not_in_allowlist" in out.reasons


def test_rules_out_of_scope_document_type() -> None:
    out = evaluate_classification_rules(
        {
            "jurisdiction": "US",
            "document_type": "spam",
            "title": "Buy now",
        }
    )
    assert out.in_scope is False
    assert "document_type_excluded" in out.reasons


def test_rules_out_of_scope_insufficient_content() -> None:
    out = evaluate_classification_rules(
        {
            "jurisdiction": "US",
            "document_type": "unknown",
            "title": None,
            "summary": "",
            "body_snippet": "   ",
        }
    )
    assert out.in_scope is False
    assert "insufficient_content" in out.reasons


def test_jurisdiction_us_ca_prefix_allowed() -> None:
    out = evaluate_classification_rules(
        {
            "jurisdiction": "US-CA",
            "document_type": "unknown",
            "title": "Hello",
        }
    )
    assert out.in_scope is True


def test_jurisdiction_none_is_permissive_in_scope() -> None:
    # Permissive MVP policy: missing jurisdiction does NOT block the LLM call —
    # items with unknown provenance still reach classification for triage. See
    # ``sentinel_prism.services.llm.rules`` module docstring.
    out = evaluate_classification_rules(
        {
            "jurisdiction": None,
            "document_type": "unknown",
            "title": "Unknown origin update",
        }
    )
    assert out.in_scope is True
    assert out.reasons == ()


def test_jurisdiction_empty_string_is_permissive_in_scope() -> None:
    out = evaluate_classification_rules(
        {
            "jurisdiction": "",
            "document_type": "unknown",
            "title": "Empty jurisdiction",
        }
    )
    assert out.in_scope is True
    assert out.reasons == ()
