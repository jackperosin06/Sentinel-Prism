"""Shared pytest fixtures.

Two autouse fixtures stub out async session factories for graph-node tests so
unit tests do not require a running Postgres. Both fixtures are scoped via the
same consolidated allowlist (:data:`_GRAPH_GRAPH_STUBBED_MODULES`) so module
renames cannot silently drift between audit and brief stubs.

Tests can also opt in explicitly with ``@pytest.mark.graph_db_stubbed`` on
individual test functions or via ``pytestmark = pytest.mark.graph_db_stubbed``
at module scope (preferred for new test files).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Tests that exercise graph nodes reaching the real audit + brief session
# factories. Consolidated (Story 4.3 review finding) so the audit stub and
# brief stub cannot drift. New test modules should prefer the explicit
# ``@pytest.mark.graph_db_stubbed`` marker over adding to this list.
_GRAPH_DB_STUBBED_MODULES = frozenset(
    {
        "test_audit_events.py",
        "test_review_queue_api.py",
        "test_graph_brief.py",
        "test_graph_classify.py",
        "test_graph_conditional_edges.py",
        "test_graph_human_review_resume.py",
        "test_graph_retry_policy.py",
        "test_graph_scout_normalize.py",
        "test_graph_shell.py",
        "test_graph_route.py",
    }
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "graph_db_stubbed: opt-in alternative to the filename allowlist for "
        "tests that reach graph nodes with DB calls but should run without "
        "a real Postgres.",
    )
    config.addinivalue_line(
        "markers",
        "integration: end-to-end test requiring DATABASE_URL + ALEMBIC_SYNC_URL.",
    )


def _should_stub_graph_db(request: pytest.FixtureRequest) -> bool:
    if request.node.get_closest_marker("integration"):
        return False
    if request.node.get_closest_marker("graph_db_stubbed"):
        return True
    fspath = getattr(request.node, "fspath", None)
    if fspath is None:
        return False
    return Path(str(fspath)).name in _GRAPH_DB_STUBBED_MODULES


def _brief_graph_db_factory() -> MagicMock:
    """Minimal async session factory for :func:`node_brief` in unit tests.

    Supports the execute-based upsert introduced by Story 4.3 Decision 4 —
    ``session.execute(stmt)`` returns a result whose ``.one()`` yields a
    ``(uuid, True)`` tuple, matching the ``RETURNING id, (xmax = 0)`` shape
    emitted by the real Postgres path.
    """

    class _EmptyScalarsResult:
        def all(self) -> list:
            return []

    async def _empty_scalars(*_a: object, **_k: object) -> _EmptyScalarsResult:
        return _EmptyScalarsResult()

    def _make_execute_result() -> SimpleNamespace:
        return SimpleNamespace(
            one=lambda: SimpleNamespace(id=uuid.uuid4(), created=True),
        )

    async def _execute(*_a: object, **_k: object) -> SimpleNamespace:
        return _make_execute_result()

    def _make_session() -> SimpleNamespace:
        # ``MagicMock`` sessions break ``await session.scalars(...)`` — use a
        # plain namespace so the real async callables are invoked.
        return SimpleNamespace(
            scalars=_empty_scalars,
            scalar=AsyncMock(return_value=None),
            execute=_execute,
            add=MagicMock(),
            flush=AsyncMock(),
            commit=AsyncMock(),
        )

    # Mirrors ``async_sessionmaker``: ``get_session_factory()`` returns a
    # callable; ``factory()`` yields the async context manager for one session.
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(side_effect=_make_session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    sessionmaker = MagicMock(return_value=session_cm)
    return MagicMock(return_value=sessionmaker)


@pytest.fixture(autouse=True)
def _mock_pipeline_audit_session_factory(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph nodes append audit rows; unit tests avoid requiring Postgres.

    Scope:
      * Active for tests in :data:`_GRAPH_DB_STUBBED_MODULES` or carrying
        ``@pytest.mark.graph_db_stubbed``; every other test sees the real
        session factory and is unaffected.
      * Skipped for tests marked ``@pytest.mark.integration`` so the
        end-to-end audit-write path runs against the real Postgres session
        factory.

    Behaviour:
      Returns a session whose ``add`` captures ORM rows, ``flush`` stamps
      ``id`` values, and ``commit`` is a no-op ``AsyncMock``. Tests that
      need to assert commit behaviour must set up their own session factory.
    """

    if not _should_stub_graph_db(request):
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
def _mock_brief_db_session_factory(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _should_stub_graph_db(request):
        return
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.brief.get_session_factory",
        _brief_graph_db_factory(),
    )


def _route_graph_db_factory() -> MagicMock:
    """Session factory for :func:`node_route` — ``execute`` supports ORM selects + audit."""

    class _Scalars:
        def all(self) -> list:
            return []

    class _ExecResult:
        def scalars(self) -> _Scalars:
            return _Scalars()

        def scalar_one_or_none(self) -> None:
            return None

    async def _execute(*_a: object, **_k: object) -> _ExecResult:
        return _ExecResult()

    def _make_session() -> SimpleNamespace:
        return SimpleNamespace(
            execute=_execute,
            add=MagicMock(),
            flush=AsyncMock(),
            commit=AsyncMock(),
        )

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(side_effect=_make_session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    sessionmaker = MagicMock(return_value=session_cm)
    return MagicMock(return_value=sessionmaker)


@pytest.fixture(autouse=True)
def _mock_route_db_session_factory(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _should_stub_graph_db(request):
        return
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.get_session_factory",
        _route_graph_db_factory(),
    )


@pytest.fixture(autouse=True)
def _clear_openai_api_key_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force classification to use the stub LLM in CI (Story 3.4)."""

    monkeypatch.setenv("OPENAI_API_KEY", "")
