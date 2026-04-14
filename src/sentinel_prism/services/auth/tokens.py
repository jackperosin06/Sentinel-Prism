"""JWT access tokens (Bearer)."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt

_ALGO_DEFAULT = "HS256"
_ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512"}
_MAX_EXPIRE_MINUTES = 10080  # 1 week

_logger = logging.getLogger(__name__)


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")
    return secret


def _algorithm() -> str:
    algo = os.environ.get("JWT_ALGORITHM", _ALGO_DEFAULT).strip() or _ALGO_DEFAULT
    if algo not in _ALLOWED_ALGORITHMS:
        raise RuntimeError(
            f"JWT_ALGORITHM must be one of {sorted(_ALLOWED_ALGORITHMS)}, got {algo!r}"
        )
    return algo


def _expire_minutes() -> int:
    raw = os.environ.get("JWT_EXPIRE_MINUTES", "60").strip()
    try:
        val = int(raw)
    except ValueError:
        _logger.warning("JWT_EXPIRE_MINUTES=%r is not a valid integer; defaulting to 60", raw)
        return 60
    if val < 1:
        _logger.warning("JWT_EXPIRE_MINUTES=%d is less than 1; clamping to 1", val)
        return 1
    if val > _MAX_EXPIRE_MINUTES:
        _logger.warning(
            "JWT_EXPIRE_MINUTES=%d exceeds maximum %d; clamping", val, _MAX_EXPIRE_MINUTES
        )
        return _MAX_EXPIRE_MINUTES
    return val


def create_access_token(user_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=_expire_minutes())
    payload = {"sub": str(user_id), "exp": exp, "iat": now}
    return jwt.encode(payload, _secret(), algorithm=_algorithm())


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=[_algorithm()])
