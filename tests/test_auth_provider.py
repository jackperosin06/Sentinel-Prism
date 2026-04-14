"""Auth provider protocol, factory, and stub (Story 1.5)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import User, UserRole
from sentinel_prism.services.auth.providers.factory import get_auth_provider
from sentinel_prism.services.auth.providers.local import LocalAuthProvider
from sentinel_prism.services.auth.providers.stub import StubAuthProvider


@pytest.mark.asyncio
async def test_stub_never_returns_user() -> None:
    stub = StubAuthProvider()
    session = AsyncMock(spec=AsyncSession)
    assert await stub.verify_email_password(session, "a@b.com", "SecretPass12345") is None


def test_factory_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    p = get_auth_provider()
    assert isinstance(p, LocalAuthProvider)


def test_factory_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "stub")
    p = get_auth_provider()
    assert isinstance(p, StubAuthProvider)


def test_factory_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    with pytest.raises(ValueError, match="Unknown AUTH_PROVIDER"):
        get_auth_provider()


@pytest.mark.asyncio
async def test_local_delegates_to_authenticate_user() -> None:
    user = User(
        id=uuid.uuid4(),
        email="u@example.com",
        password_hash="x",
        role=UserRole.VIEWER,
        is_active=True,
    )
    session = AsyncMock(spec=AsyncSession)
    with patch(
        "sentinel_prism.services.auth.providers.local.authenticate_user",
        new_callable=AsyncMock,
        return_value=user,
    ) as mock_auth:
        local = LocalAuthProvider()
        out = await local.verify_email_password(session, "u@example.com", "pw")
    mock_auth.assert_awaited_once_with(session, "u@example.com", "pw")
    assert out is user
