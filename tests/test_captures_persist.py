"""Raw + normalized persistence (Story 3.1) — optional DB integration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel_prism.db.models import NormalizedUpdateRow, RawCapture, Source, SourceType
from sentinel_prism.db.repositories import ingestion_dedup
from sentinel_prism.db.repositories import captures as captures_repo
from sentinel_prism.services.connectors.scout_raw_item import (
    ScoutRawItem,
    scout_raw_item_from_payload,
)
from sentinel_prism.services.ingestion.persist import persist_new_items_after_dedup

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
async def test_persist_after_dedup_links_raw_to_normalized() -> None:
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

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    sid: uuid.UUID | None = None

    try:
        async with factory() as session:
            src = Source(
                name=f"captures-test-{suffix}",
                jurisdiction="CH",
                source_type=SourceType.RSS,
                primary_url="https://example.com/feed",
                schedule="0 * * * *",
                enabled=True,
            )
            session.add(src)
            await session.commit()
            sid = src.id

            # Two items — lets us also assert per-row raw↔normalized alignment
            # and exercise the multi-item insert loop in persist.
            fetched = datetime.now(timezone.utc)
            items = [
                ScoutRawItem(
                    source_id=sid,
                    item_url=f"https://reg.example/persist-{i}",
                    fetched_at=fetched,
                    title=f"Persisted {i}",
                    published_at=fetched,
                    summary=f"S{i}",
                    body_snippet=f"B{i}",
                    http_status=200,
                    content_type="text/xml",
                )
                for i in range(2)
            ]
            new_items = await ingestion_dedup.register_new_items(session, sid, items)
            assert len(new_items) == 2

            await persist_new_items_after_dedup(
                session,
                source_id=sid,
                source_name=src.name,
                jurisdiction=src.jurisdiction,
                new_items=new_items,
            )
            await session.commit()

            n_raw = (
                await session.execute(
                    select(func.count()).select_from(RawCapture).where(
                        RawCapture.source_id == sid
                    )
                )
            ).scalar_one()
            n_norm = (
                await session.execute(
                    select(func.count())
                    .select_from(NormalizedUpdateRow)
                    .where(NormalizedUpdateRow.source_id == sid)
                )
            ).scalar_one()
            assert n_raw == 2
            assert n_norm == 2

            # Per-item alignment: the normalized row for item N points at the
            # raw capture for item N (no cross-linking).
            for item in items:
                row_norm = (
                    await session.execute(
                        select(NormalizedUpdateRow).where(
                            NormalizedUpdateRow.item_url == item.item_url
                        )
                    )
                ).scalar_one()
                assert row_norm.source_id == sid
                assert row_norm.source_name == src.name
                assert row_norm.jurisdiction == "CH"
                assert row_norm.title == item.title
                assert row_norm.body_snippet == item.body_snippet
                # Confidence fields are MVP-heuristic floats; the contract is
                # ``0 <= value <= 1`` (heuristic actually tops out at 0.95).
                assert row_norm.parser_confidence is not None
                assert 0.0 <= row_norm.parser_confidence <= 1.0
                assert row_norm.extraction_quality is not None
                assert 0.0 <= row_norm.extraction_quality <= 1.0

                raw = await session.get(RawCapture, row_norm.raw_capture_id)
                assert raw is not None
                assert raw.source_id == sid
                assert raw.item_url == item.item_url
                # Timezone round-trip: ``captured_at`` is tz-aware and equal to
                # the scout ``fetched_at``; the JSONB payload isoformat also
                # rehydrates to a tz-aware datetime.
                assert raw.captured_at.tzinfo is not None
                assert raw.captured_at == item.fetched_at
                payload_fetched = datetime.fromisoformat(raw.payload["fetched_at"])
                assert payload_fetched.tzinfo is not None
                assert payload_fetched == item.fetched_at
                # Payload drift-proofing: every ``ScoutRawItem`` field is present.
                rehydrated = scout_raw_item_from_payload(dict(raw.payload))
                assert rehydrated == item

        # UNIQUE(raw_capture_id) enforcement: a second normalized row pointing
        # at the same raw capture must be rejected by the DB.
        async with factory() as session:
            first_raw = (
                await session.execute(
                    select(RawCapture).where(RawCapture.source_id == sid).limit(1)
                )
            ).scalar_one()
            dup = NormalizedUpdateRow(
                raw_capture_id=first_raw.id,
                source_id=sid,
                source_name="dup",
                jurisdiction="CH",
                title="dup",
                published_at=None,
                item_url=first_raw.item_url,
                document_type="unknown",
                body_snippet=None,
                summary=None,
                extra_metadata=None,
                parser_confidence=None,
                extraction_quality=None,
                run_id=None,
            )
            session.add(dup)
            with pytest.raises(IntegrityError):
                await session.commit()

        # ON DELETE RESTRICT enforcement: source delete must fail while
        # raw_captures / normalized_updates still reference it.
        async with factory() as session:
            src_row = await session.get(Source, sid)
            assert src_row is not None
            await session.delete(src_row)
            with pytest.raises(IntegrityError):
                await session.commit()

        # Source-id mismatch defence-in-depth: insert_raw_capture rejects an
        # item whose ``source_id`` disagrees with the persist context.
        async with factory() as session:
            other = uuid.uuid4()
            mismatched = ScoutRawItem(
                source_id=other,
                item_url="https://reg.example/mismatch",
                fetched_at=datetime.now(timezone.utc),
            )
            with pytest.raises(ValueError):
                await captures_repo.insert_raw_capture(
                    session, source_id=sid, item=mismatched
                )
    finally:
        # Teardown: delete normalized → raw → source to satisfy RESTRICT FKs.
        if sid is not None:
            async with factory() as session:
                await session.execute(
                    delete(NormalizedUpdateRow).where(
                        NormalizedUpdateRow.source_id == sid
                    )
                )
                await session.execute(
                    delete(RawCapture).where(RawCapture.source_id == sid)
                )
                await session.execute(delete(Source).where(Source.id == sid))
                await session.commit()
        await engine.dispose()
