"""Connector-level errors (Story 2.4 — FR4)."""

from __future__ import annotations


class ConnectorFetchFailed(Exception):
    """Raised when a fetch exhausts retries or hits a non-retryable HTTP outcome.

    ``execute_poll`` catches this to persist ``last_poll_failure`` and avoid
    treating the outcome as a silent empty feed.
    """

    def __init__(self, message: str, *, error_class: str) -> None:
        super().__init__(message)
        self.error_class = error_class
