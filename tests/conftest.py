"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_openai_api_key_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force classification to use the stub LLM in CI (Story 3.4)."""

    monkeypatch.setenv("OPENAI_API_KEY", "")
