"""Ingestion dedup persistence (Story 2.4) — optional DB integration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel_prism.db.models import Source, SourceType
from sentinel_prism.db.repositories import ingestion_dedup

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
async def test_register_new_items_skips_duplicate_fingerprint() -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    sync_url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not db_url or not sync_url:
        pytest.skip("DATABASE_URL and ALEMBIC_SYNC_URL required for integration")

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

    from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]

    async with factory() as session:
        src = Source(
            name=f"dedup-test-{suffix}",
            jurisdiction="EU",
            source_type=SourceType.RSS,
            primary_url="https://example.com/feed",
            schedule="0 * * * *",
            enabled=True,
        )
        session.add(src)
        await session.commit()
        sid = src.id

        fetched = datetime.now(timezone.utc)
        item = ScoutRawItem(
            source_id=sid,
            item_url="https://reg.example/item-1",
            fetched_at=fetched,
            title="Hello",
            summary="World",
        )
        new1 = await ingestion_dedup.register_new_items(session, sid, [item])
        await session.commit()
        assert len(new1) == 1

        new2 = await ingestion_dedup.register_new_items(session, sid, [item])
        await session.commit()
        assert new2 == []

    await engine.dispose()
