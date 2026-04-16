"""SQLAlchemy ORM models and shared metadata."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, String, Text, func
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
    schedule: Mapped[str] = mapped_column(String(512), nullable=False)
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
