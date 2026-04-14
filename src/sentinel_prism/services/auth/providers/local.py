"""Local password auth provider (delegates to Story 1.3 service)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import User
from sentinel_prism.services.auth.service import authenticate_user


class LocalAuthProvider:
    async def verify_email_password(
        self,
        session: AsyncSession,
        email: str,
        password: str,
    ) -> User | None:
        return await authenticate_user(session, email, password)
