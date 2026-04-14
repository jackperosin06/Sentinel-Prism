"""Auth provider implementations and factory."""

from sentinel_prism.services.auth.providers.factory import get_auth_provider
from sentinel_prism.services.auth.providers.local import LocalAuthProvider
from sentinel_prism.services.auth.providers.protocol import AuthProvider
from sentinel_prism.services.auth.providers.stub import StubAuthProvider

__all__ = [
    "AuthProvider",
    "LocalAuthProvider",
    "StubAuthProvider",
    "get_auth_provider",
]
