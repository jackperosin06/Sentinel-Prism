"""SQLAlchemy ORM models and shared metadata."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UserRole(StrEnum):
    """RBAC roles (Story 1.4). Stored as VARCHAR; no PostgreSQL native enum type."""

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class SourceType(StrEnum):
    """Public regulatory source connector kind (Story 2.1)."""

    RSS = "rss"
    HTTP = "http"


class FallbackMode(StrEnum):
    """Alternate fetch when primary URL fails (Story 2.5 — FR5)."""

    NONE = "none"
    SAME_AS_PRIMARY = "same_as_primary"
    HTML_PAGE = "html_page"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


metadata = Base.metadata


def _str_enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """SQLAlchemy ``Enum`` persists member *names* by default; DB check constraints use *values*."""

    return [m.value for m in enum_cls]


class User(Base):
    """Local user account (email + password) with RBAC role (Story 1.4)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            native_enum=False,
            length=32,
            values_callable=_str_enum_values,
        ),
        nullable=False,
        server_default=UserRole.VIEWER.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Source(Base):
    """Registered public ingestion source (Story 2.1 — FR1)."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    jurisdiction: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            native_enum=False,
            length=32,
            values_callable=_str_enum_values,
        ),
        nullable=False,
    )
    primary_url: Mapped[str] = mapped_column(Text(), nullable=False)
    fallback_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    fallback_mode: Mapped[FallbackMode] = mapped_column(
        Enum(
            FallbackMode,
            native_enum=False,
            length=32,
            values_callable=_str_enum_values,
        ),
        nullable=False,
        default=FallbackMode.NONE,
        server_default=FallbackMode.NONE.value,
    )
    schedule: Mapped[str] = mapped_column(String(512), nullable=False)
    poll_attempts_success: Mapped[int] = mapped_column(
        BigInteger(), nullable=False, default=0, server_default="0"
    )
    poll_attempts_failed: Mapped[int] = mapped_column(
        BigInteger(), nullable=False, default=0, server_default="0"
    )
    items_ingested_total: Mapped[int] = mapped_column(
        BigInteger(), nullable=False, default=0, server_default="0"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_latency_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    last_success_fetch_path: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # ``none_as_null=True`` so Python ``None`` maps to SQL NULL rather than JSON
    # ``'null'``. Without this, ``record_poll_failure``'s ``coalesce(extra_metadata,
    # '{}'::jsonb) || patch`` would see JSONB ``null`` (coalesce only masks SQL NULL)
    # and the ``||`` operator would promote both sides to single-element arrays,
    # yielding ``[null, {...}]`` instead of a merged object (breaks the dict contract
    # exposed via ``SourceResponse.extra_metadata``).
    extra_metadata: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SourceIngestedFingerprint(Base):
    """Lightweight idempotency ledger for connector output (Story 2.4 — FR3)."""

    __tablename__ = "source_ingested_fingerprints"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "fingerprint",
            name="uq_source_ingested_fingerprint_source_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        # No separate index: the UNIQUE (source_id, fingerprint) constraint creates
        # a covering index usable for prefix scans on source_id. A redundant B-tree
        # index would add write overhead on every INSERT.
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    item_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RawCapture(Base):
    """Durable scout output before normalization (Story 3.1 — FR7).

    ``payload`` JSONB holds a serialized ``ScoutRawItem``-compatible dict (reconstructible).
    ``sources`` row deletion is RESTRICTed so audit captures are not silently removed.
    Optional ``run_id`` reserved for LangGraph correlation (Story 3.2+); unused until then.
    """

    __tablename__ = "raw_captures"
    __table_args__ = (Index("ix_raw_captures_source_captured", "source_id", "captured_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    item_url: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NormalizedUpdateRow(Base):
    """Normalized regulatory update derived from a raw capture (Story 3.1 — FR8, FR10).

    ``raw_capture_id`` is UNIQUE: spec AC4 pins a one-to-one link raw→normalized for MVP.
    ON DELETE RESTRICT on both FKs keeps the audit chain intact.

    ``source_name`` and ``jurisdiction`` are **point-in-time denormalized snapshots**
    taken when the capture was normalized; a later admin rename/re-jurisdiction of
    ``sources`` will NOT back-propagate, intentionally preserving audit fidelity.

    ``body_snippet`` is an excerpt (typically first 2–3 sentences) as produced by
    ``ScoutRawItem.body_snippet``; full-body extraction lands in Story 3.3+.

    ``parser_confidence`` / ``extraction_quality`` hold MVP heuristic floats in
    ``[0, 0.95]`` (the heuristic weights cap at 0.95 by design; the 1.0 headroom is
    reserved for future non-MVP pipelines). Both columns are nullable so future
    non-heuristic paths may legitimately omit one or both.
    """

    __tablename__ = "normalized_updates"
    __table_args__ = (
        Index("ix_normalized_updates_source_created", "source_id", "created_at"),
        UniqueConstraint(
            "raw_capture_id", name="uq_normalized_updates_raw_capture_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    raw_capture_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_captures.id", ondelete="RESTRICT"),
        nullable=False,
        # No separate index: UNIQUE (raw_capture_id) creates a covering B-tree.
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text(), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    item_url: Mapped[str] = mapped_column(Text(), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    body_snippet: Mapped[str | None] = mapped_column(Text(), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # ``none_as_null=True`` matches ``Source.extra_metadata`` — a missing metadata
    # payload is SQL NULL, never JSON ``'null'`` (the latter would silently poison
    # any future JSONB merge with ``||``).
    extra_metadata: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    parser_confidence: Mapped[float | None] = mapped_column(Float(), nullable=True)
    extraction_quality: Mapped[float | None] = mapped_column(Float(), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
