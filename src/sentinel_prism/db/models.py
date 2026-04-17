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
    ForeignKey,
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
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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
