"""Dashboard summary API (Story 6.1 — FR30, FR40)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sentinel_prism.db.models import (
    AuditEvent,
    NormalizedUpdateRow,
    PipelineAuditAction,
    RawCapture,
    ReviewQueueItem,
    Source,
    SourceType,
)
from sentinel_prism.db.repositories.dashboard import _merge_severity_histograms
from sentinel_prism.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_merge_severity_histograms_accepts_jsonb_text_values() -> None:
    """Some DB drivers return JSONB as text; merge should parse those rows."""

    merged = _merge_severity_histograms(
        [
            '{"severity_histogram":{"high":2}}',
            {"severity_histogram": {"medium": 1}},
        ]
    )
    assert merged == {"high": 2, "medium": 1}


@pytest.mark.asyncio
async def test_dashboard_summary_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/dashboard/summary")
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dashboard_summary_ok_seeded_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed DB rows and verify end-to-end dashboard aggregation without repo mocks.

    Seeds run through the same async engine the API uses so every commit is on the
    connection pool the FastAPI request handler reads from — the previous sync-engine
    seed + ``LifespanManager`` reentry pattern surfaced a reproducible case where the
    dashboard response did not reflect freshly-committed audit events (seen as the
    severity delta coming back as ``(0 - 0) == 2``).

    Assertions are baseline-relative so pre-existing integration data does not make
    this test flaky across repeated full-suite runs; the ``finally`` block fully
    cleans up every row we inserted.
    """

    db_url = os.environ.get("DATABASE_URL", "").strip()
    sync_url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not db_url or not sync_url:
        pytest.skip("DATABASE_URL and ALEMBIC_SYNC_URL required for integration")

    monkeypatch.setenv("JWT_SECRET", "integration-test-jwt-secret-32chars-min")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    monkeypatch.setenv("DASHBOARD_NEW_ITEMS_HOURS", "24")

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "ALEMBIC_SYNC_URL": sync_url}
    mig = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert mig.returncode == 0, mig.stderr

    import sentinel_prism.db.session as session_mod

    session_mod._engine = None  # type: ignore[attr-defined]
    session_mod._session_factory = None  # type: ignore[attr-defined]

    # Share one async engine with the app. Registering it as the module-level
    # singleton guarantees that ``get_db`` inside the FastAPI route reads from
    # the same pool we just seeded through, so a commit here is immediately
    # visible to the next request (no cross-driver, cross-loop games).
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_mod._engine = engine  # type: ignore[attr-defined]
    session_mod._session_factory = factory  # type: ignore[attr-defined]

    suffix = uuid.uuid4().hex[:8]
    email = f"viewer{suffix}@example.com"
    password = "SecretPass1Ab"
    source_a_name = f"FDA-{suffix}"
    source_b_name = f"EMA-{suffix}"
    source_a = uuid.uuid4()
    source_b = uuid.uuid4()
    run_latest = uuid.uuid4()
    run_other = uuid.uuid4()
    run_queue = uuid.uuid4()
    raw_recent = uuid.uuid4()
    raw_old = uuid.uuid4()
    now = datetime.now(timezone.utc)

    app = create_app()
    transport = ASGITransport(app=app)

    # Psycopg is used purely for the privileged ``UPDATE users`` (role escalation)
    # and the final cleanup; neither path needs to coordinate with async reads.
    sync_engine = create_engine(sync_url)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post(
                "/auth/register", json={"email": email, "password": password}
            )
            assert reg.status_code == 201, reg.text

        with sync_engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET role = 'viewer' WHERE email = :e"),
                {"e": email},
            )

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/auth/login",
                json={"email": email, "password": password},
            )
            assert login.status_code == 200, login.text
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            baseline_resp = await client.get(
                "/dashboard/summary?top_sources_limit=50",
                headers=headers,
            )
            assert baseline_resp.status_code == 200
            baseline = baseline_resp.json()

            # Seed through the same async engine/session the API route reads from.
            # A single ``session.commit()`` flushes everything atomically before
            # we issue the follow-up GET, so any visibility concern collapses to
            # Postgres READ COMMITTED semantics on the same pooled connection.
            async with factory() as session:
                session.add_all(
                    [
                        Source(
                            id=source_a,
                            name=source_a_name,
                            jurisdiction="US",
                            source_type=SourceType.RSS,
                            primary_url=f"https://example.test/{suffix}/fda.xml",
                            schedule="0 * * * *",
                            items_ingested_total=9_000_000_011,
                        ),
                        Source(
                            id=source_b,
                            name=source_b_name,
                            jurisdiction="EU",
                            source_type=SourceType.RSS,
                            primary_url=f"https://example.test/{suffix}/ema.xml",
                            schedule="0 * * * *",
                            items_ingested_total=9_000_000_010,
                        ),
                    ]
                )
                await session.flush()

                session.add_all(
                    [
                        RawCapture(
                            id=raw_recent,
                            source_id=source_a,
                            captured_at=now,
                            item_url=f"https://example.test/{suffix}/fda/recent",
                            payload={"title": "recent"},
                            run_id=run_latest,
                        ),
                        RawCapture(
                            id=raw_old,
                            source_id=source_b,
                            captured_at=now - timedelta(hours=72),
                            item_url=f"https://example.test/{suffix}/ema/old",
                            payload={"title": "old"},
                            run_id=run_other,
                        ),
                    ]
                )
                await session.flush()

                session.add_all(
                    [
                        NormalizedUpdateRow(
                            id=uuid.uuid4(),
                            raw_capture_id=raw_recent,
                            source_id=source_a,
                            source_name=source_a_name,
                            jurisdiction="US",
                            item_url=f"https://example.test/{suffix}/fda/recent",
                            document_type="notice",
                            run_id=run_latest,
                            created_at=now,
                        ),
                        NormalizedUpdateRow(
                            id=uuid.uuid4(),
                            raw_capture_id=raw_old,
                            source_id=source_b,
                            source_name=source_b_name,
                            jurisdiction="EU",
                            item_url=f"https://example.test/{suffix}/ema/old",
                            document_type="notice",
                            run_id=run_other,
                            created_at=now - timedelta(hours=72),
                        ),
                    ]
                )

                session.add(
                    ReviewQueueItem(
                        run_id=run_queue,
                        source_id=source_a,
                        items_summary=[],
                        queued_at=now,
                    )
                )

                session.add_all(
                    [
                        AuditEvent(
                            id=uuid.uuid4(),
                            run_id=run_latest,
                            action=PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED,
                            source_id=source_a,
                            event_metadata={"severity_histogram": {"low": 99}},
                            created_at=now - timedelta(minutes=10),
                        ),
                        AuditEvent(
                            id=uuid.uuid4(),
                            run_id=run_latest,
                            action=PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED,
                            source_id=source_a,
                            event_metadata={"severity_histogram": {"high": 2}},
                            created_at=now - timedelta(minutes=5),
                        ),
                        AuditEvent(
                            id=uuid.uuid4(),
                            run_id=run_other,
                            action=PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED,
                            source_id=source_b,
                            event_metadata={"severity_histogram": {"medium": 1}},
                            created_at=now - timedelta(minutes=2),
                        ),
                    ]
                )

                await session.commit()

            resp = await client.get(
                "/dashboard/summary?top_sources_limit=50",
                headers=headers,
            )
            assert resp.status_code == 200
            body = resp.json()

        base_severity = baseline["severity_counts"]
        post_severity = body["severity_counts"]
        assert post_severity.get("high", 0) - base_severity.get("high", 0) == 2
        assert post_severity.get("medium", 0) - base_severity.get("medium", 0) == 1
        # ``run_latest`` has an older ``low`` event superseded by a newer one.
        assert post_severity.get("low", 0) == base_severity.get("low", 0)

        assert body["new_items_count"] - baseline["new_items_count"] == 1
        assert body["new_items_window_hours"] == 24
        assert body["review_queue_count"] - baseline["review_queue_count"] == 1
        assert body["top_sources_metric"] == "items_ingested_total"

        got_top = {row["name"]: row["value"] for row in body["top_sources"]}
        assert got_top.get(source_a_name) == 9_000_000_011
        assert got_top.get(source_b_name) == 9_000_000_010
        assert all(row["metric"] == "items_ingested_total" for row in body["top_sources"])
    finally:
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM audit_events "
                        "WHERE run_id IN (:r1, :r2) OR source_id IN (:s1, :s2)"
                    ),
                    {
                        "r1": run_latest,
                        "r2": run_other,
                        "s1": source_a,
                        "s2": source_b,
                    },
                )
                conn.execute(
                    text("DELETE FROM review_queue_items WHERE run_id = :rq"),
                    {"rq": run_queue},
                )
                conn.execute(
                    text("DELETE FROM normalized_updates WHERE run_id IN (:r1, :r2)"),
                    {"r1": run_latest, "r2": run_other},
                )
                conn.execute(
                    text("DELETE FROM raw_captures WHERE run_id IN (:r1, :r2)"),
                    {"r1": run_latest, "r2": run_other},
                )
                conn.execute(
                    text("DELETE FROM sources WHERE id IN (:s1, :s2)"),
                    {"s1": source_a, "s2": source_b},
                )
                conn.execute(
                    text("DELETE FROM users WHERE email = :e"), {"e": email}
                )
        finally:
            sync_engine.dispose()
            await engine.dispose()
            session_mod._engine = None  # type: ignore[attr-defined]
            session_mod._session_factory = None  # type: ignore[attr-defined]
