"""SQLAlchemy ORM models and shared metadata."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UserRole(StrEnum):
    """RBAC roles (Story 1.4). Stored as VARCHAR; no PostgreSQL native enum type."""

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


metadata = Base.metadata


class User(Base):
    """Local user account (email + password) with RBAC role (Story 1.4)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=32),
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
