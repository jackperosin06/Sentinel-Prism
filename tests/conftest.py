"""Shared pytest fixtures."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Only these test modules exercise graph nodes that call
# ``pipeline_audit.get_session_factory`` / ``pipeline_review.get_session_factory``
# at runtime. The autouse fixture below is a no-op for every other test so
# Story 3.8/4.1 mocking does not globalize onto unrelated unit tests.
_GRAPH_TEST_MODULE_BASENAMES = frozenset(
    {
        "test_audit_events.py",
        "test_review_queue_api.py",
        "test_graph_classify.py",
        "test_graph_conditional_edges.py",
        "test_graph_retry_policy.py",
        "test_graph_scout_normalize.py",
        "test_graph_shell.py",
    }
)


@pytest.fixture(autouse=True)
def _mock_pipeline_audit_session_factory(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph nodes append audit rows; unit tests avoid requiring Postgres.

    Scope:
      * Active only for tests that exercise the graph (see
        ``_GRAPH_TEST_MODULE_BASENAMES``); every other test module sees the
        real session factory and is unaffected.
      * Skipped for tests marked ``@pytest.mark.integration`` so the
        end-to-end audit-write path in ``tests/test_audit_events.py``'s
        integration test runs against the real Postgres session factory.

    Behaviour:
      Returns a session whose ``add`` captures ORM rows, ``flush`` stamps
      ``id`` values, and ``commit`` is a no-op ``AsyncMock``. Tests that
      need to assert commit behaviour must set up their own session factory.
    """

    if request.node.get_closest_marker("integration"):
        return
    fspath = getattr(request.node, "fspath", None)
    if fspath is None:
        return
    basename = Path(str(fspath)).name
    if basename not in _GRAPH_TEST_MODULE_BASENAMES:
        return

    def _factory() -> MagicMock:
        session = MagicMock()
        added: list[object] = []

        def _add(row: object) -> None:
            added.append(row)

        session.add.side_effect = _add

        async def _flush() -> None:
            for row in added:
                if getattr(row, "id", None) is None and hasattr(row, "id"):
                    row.id = uuid.uuid4()

        session.commit = AsyncMock()
        session.flush = AsyncMock(side_effect=_flush)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return MagicMock(return_value=cm)

    monkeypatch.setattr(
        "sentinel_prism.graph.pipeline_audit.get_session_factory",
        _factory,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.pipeline_review.get_session_factory",
        _factory,
    )


@pytest.fixture(autouse=True)
def _clear_openai_api_key_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force classification to use the stub LLM in CI (Story 3.4)."""

    monkeypatch.setenv("OPENAI_API_KEY", "")
