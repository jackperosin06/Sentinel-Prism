"""User registration and authentication (local verify only)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import User, UserRole
from sentinel_prism.services.auth.passwords import hash_password, verify_password


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    email: str,
    password: str,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=UserRole.VIEWER,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate_user(
    session: AsyncSession, email: str, password: str
) -> User | None:
    user = await get_user_by_email(session, email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
