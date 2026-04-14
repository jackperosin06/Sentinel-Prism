"""FastAPI dependencies (DB sessions, auth, etc.)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import User, UserRole
from sentinel_prism.db.session import get_db
from sentinel_prism.services.auth import decode_access_token, get_user_by_id
from sentinel_prism.services.auth.providers.factory import get_auth_provider
from sentinel_prism.services.auth.providers.protocol import AuthProvider

bearer_scheme = HTTPBearer(auto_error=False)


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
    db: AsyncSession = Depends(get_db),
) -> User:
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
