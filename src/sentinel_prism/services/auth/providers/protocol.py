"""Pluggable auth provider protocol (Story 1.5 — NFR14)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import User


class AuthProvider(Protocol):
    """Verify email/password credentials and return the authenticated user, if any."""

    async def verify_email_password(
        self,
        session: AsyncSession,
        email: str,
        password: str,
    ) -> User | None:
        """Return the authenticated ``User``, or ``None`` on failure.

        ``email`` must already be normalized (lowercased) by the caller before
        this method is invoked.  Future IdP implementations should follow the
        same contract.
        """
        ...
