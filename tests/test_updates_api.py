"""Update explorer API (Story 6.2 — FR9, FR31, FR40)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.api.routes import updates as updates_route
from sentinel_prism.db.models import (
    Briefing,
    NormalizedUpdateRow,
    RawCapture,
    Source,
    SourceType,
    UserRole,
)
from sentinel_prism.db.repositories.updates import ExplorerListPage
from sentinel_prism.db.session import get_db
from sentinel_prism.main import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_updates_list_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/updates")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_updates_list_rejects_inverted_created_range() -> None:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role=UserRole.VIEWER
    )
    app.dependency_overrides[get_db] = lambda: object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/updates?created_from=2026-04-27T00:00:00Z&created_to=2026-04-26T00:00:00Z"
        )
    assert r.status_code == 400
    assert "created_from" in r.text
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_updates_list_forwards_pagination_and_filter_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_fetch_updates_page(_session: object, **kwargs: object) -> ExplorerListPage:
        captured.update(kwargs)
        return ExplorerListPage(
            items=[],
            total=0,
            limit=int(kwargs["limit"]),
            offset=int(kwargs["offset"]),
            sort=kwargs["sort"],  # type: ignore[arg-type]
            default_sort="created_at_desc",
        )

    monkeypatch.setattr(
        updates_route.updates_repo,
        "fetch_updates_page",
        fake_fetch_updates_page,
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role=UserRole.VIEWER
    )
    app.dependency_overrides[get_db] = lambda: object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/updates?limit=25&offset=50&sort=published_at_asc"
            "&source_name_contains=FDA&explorer_status=processed"
            "&include_unknown_severity=true"
        )
    assert r.status_code == 200, r.text
    assert captured["limit"] == 25
    assert captured["offset"] == 50
    assert captured["sort"] == "published_at_asc"
    assert captured["source_name_contains"] == "FDA"
    assert captured["explorer_status"] == "processed"
    assert captured["include_unknown_severity"] is True
    app.dependency_overrides.clear()


def test_source_name_like_filter_escapes_user_wildcards() -> None:
    assert updates_route.updates_repo._escape_like(r"FDA_%") == r"FDA\_\%"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_updates_list_and_detail_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    sync_url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not db_url or not sync_url:
        pytest.skip("DATABASE_URL and ALEMBIC_SYNC_URL required for integration")

    monkeypatch.setenv("JWT_SECRET", "integration-test-jwt-secret-32chars-min")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

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

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_mod._engine = engine  # type: ignore[attr-defined]
    session_mod._session_factory = factory  # type: ignore[attr-defined]

    suffix = uuid.uuid4().hex[:8]
    email = f"viewer-upd{suffix}@example.com"
    password = "SecretPass1Ab"
    source_id = uuid.uuid4()
    run_id = uuid.uuid4()
    raw_id = uuid.uuid4()
    norm_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    source_name = f"SRC-{suffix}"

    app = create_app()
    transport = ASGITransport(app=app)
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

            async with factory() as session:
                session.add(
                    Source(
                        id=source_id,
                        name=source_name,
                        jurisdiction="US",
                        source_type=SourceType.RSS,
                        primary_url=f"https://example.test/{suffix}/feed.xml",
                        schedule="0 * * * *",
                        items_ingested_total=0,
                    )
                )
                await session.flush()
                session.add(
                    RawCapture(
                        id=raw_id,
                        source_id=source_id,
                        captured_at=now,
                        item_url=f"https://example.test/{suffix}/item",
                        payload={"title": "Raw title", "body": "raw body"},
                        run_id=run_id,
                    )
                )
                await session.flush()
                session.add(
                    NormalizedUpdateRow(
                        id=norm_id,
                        raw_capture_id=raw_id,
                        source_id=source_id,
                        source_name=source_name,
                        jurisdiction="US",
                        item_url=f"https://example.test/{suffix}/item",
                        document_type="guidance",
                        title="Norm title",
                        run_id=run_id,
                        created_at=now,
                    )
                )
                session.add(
                    Briefing(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        source_id=source_id,
                        created_at=now,
                        grouping_dimensions=["severity"],
                        groups=[
                            {
                                "dimensions": {"severity": "high"},
                                "sections": {
                                    "what_changed": "x",
                                    "why_it_matters": "y",
                                    "who_should_care": "z",
                                    "confidence": "0.9",
                                    "suggested_actions": None,
                                },
                                "members": [
                                    {
                                        "normalized_update_id": str(norm_id),
                                        "item_url": f"https://example.test/{suffix}/item",
                                        "title": "Norm title",
                                        "severity": "high",
                                        "confidence": 0.9,
                                        "impact_categories": ["labeling"],
                                    }
                                ],
                            }
                        ],
                    )
                )
                await session.commit()

            lst = await client.get(
                f"/updates?jurisdiction=US&document_type=guidance",
                headers=headers,
            )
            assert lst.status_code == 200, lst.text
            body = lst.json()
            assert body["total"] >= 1
            ids = {str(x["id"]) for x in body["items"]}
            assert str(norm_id) in ids
            match = next(x for x in body["items"] if x["id"] == str(norm_id))
            assert match["explorer_status"] == "briefed"
            assert match["derived_severity"] == "high"

            sev = await client.get("/updates?severity=high", headers=headers)
            assert sev.status_code == 200
            sev_ids = {x["id"] for x in sev.json()["items"]}
            assert str(norm_id) in sev_ids

            det = await client.get(f"/updates/{norm_id}", headers=headers)
            assert det.status_code == 200
            detail = det.json()
            assert detail["raw_payload"]["title"] == "Raw title"
            assert detail["normalized"]["title"] == "Norm title"
            assert detail["classification"] is not None
            assert detail["classification"]["severity"] == "high"

            missing = await client.get(
                f"/updates/{uuid.uuid4()}",
                headers=headers,
            )
            assert missing.status_code == 404
    finally:
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM briefings WHERE run_id = :r"),
                    {"r": run_id},
                )
                conn.execute(
                    text("DELETE FROM normalized_updates WHERE id = :i"),
                    {"i": norm_id},
                )
                conn.execute(
                    text("DELETE FROM raw_captures WHERE id = :i"),
                    {"i": raw_id},
                )
                conn.execute(
                    text("DELETE FROM sources WHERE id = :i"),
                    {"i": source_id},
                )
                conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})
        finally:
            sync_engine.dispose()
            await engine.dispose()
            session_mod._engine = None  # type: ignore[attr-defined]
            session_mod._session_factory = None  # type: ignore[attr-defined]
