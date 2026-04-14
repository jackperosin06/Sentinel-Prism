"""Stub provider: never authenticates (extension-point placeholder)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import User


class StubAuthProvider:
    async def verify_email_password(
        self,
        session: AsyncSession,
        email: str,
        password: str,
    ) -> User | None:
        return None
