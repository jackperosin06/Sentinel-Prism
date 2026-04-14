"""Resolve active AuthProvider from environment (single wiring point)."""

from __future__ import annotations

import os

from sentinel_prism.services.auth.providers.local import LocalAuthProvider
from sentinel_prism.services.auth.providers.protocol import AuthProvider
from sentinel_prism.services.auth.providers.stub import StubAuthProvider


def get_auth_provider() -> AuthProvider:
    """Return the credential verifier for ``POST /auth/login``.

    ``AUTH_PROVIDER`` env:

    - ``local`` (default) — email/password against the users table
    - ``stub`` — always fails verification (tests / future IdP shell)
    """

    name = os.environ.get("AUTH_PROVIDER", "local").strip().lower()
    if name in ("", "local"):
        return LocalAuthProvider()
    if name == "stub":
        return StubAuthProvider()
    raise ValueError(
        f"Unknown AUTH_PROVIDER {name!r}; supported values: 'local', 'stub'."
    )
