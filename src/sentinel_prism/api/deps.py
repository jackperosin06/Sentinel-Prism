"""FastAPI dependencies (DB sessions, auth, etc.)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Literal, Protocol

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import User, UserRole
from sentinel_prism.db.session import get_db, get_session_factory
from sentinel_prism.services.auth import decode_access_token, get_user_by_id
from sentinel_prism.services.auth.providers.factory import get_auth_provider
from sentinel_prism.services.auth.providers.protocol import AuthProvider
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

bearer_scheme = HTTPBearer(auto_error=False)


class PollExecutor(Protocol):
    async def __call__(
        self,
        source_id: uuid.UUID,
        *,
        trigger: Literal["scheduled", "manual"],
    ) -> list[ScoutRawItem]: ...


def get_poll_executor() -> PollExecutor:
    from sentinel_prism.services.connectors.poll import execute_poll

    return execute_poll


def get_login_auth_provider() -> AuthProvider:
    """Active credential verifier for login (from ``AUTH_PROVIDER``)."""

    return get_auth_provider()


def require_roles(
    *allowed: UserRole,
) -> Callable[..., Awaitable[User]]:
    """Dependency factory: authenticated user must have one of the given roles (403 otherwise)."""

    if not allowed:
        raise ValueError("require_roles() must be called with at least one UserRole")
    allowed_set = frozenset(allowed)

    async def checker(current: User = Depends(get_current_user)) -> User:
        if current.role not in allowed_set:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current

    return checker


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """Resolve JWT to a ``User`` row. Opens a DB session only after the token parses."""

    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials)
        sub = payload.get("sub")
        if not sub:
            raise ValueError("missing sub")
        user_id = uuid.UUID(str(sub))
    except (ValueError, jwt.PyJWTError, TypeError):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    factory = get_session_factory()
    async with factory() as db:
        user = await get_user_by_id(db, user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            UserRole(user.role)
        except ValueError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Account has an invalid role; contact an administrator",
            )
        return user


async def get_db_for_admin(
    _user: User = Depends(require_roles(UserRole.ADMIN)),
) -> AsyncSession:  # type: ignore[return]
    """DB session for routes that require **admin** — RBAC runs before this session opens.

    Declared as ``-> AsyncSession`` so ``Annotated[AsyncSession, Depends(...)]`` is
    type-correct from the caller's perspective. FastAPI detects the ``yield`` and manages
    the generator lifecycle correctly at runtime.
    """

    async for session in get_db():
        yield session
