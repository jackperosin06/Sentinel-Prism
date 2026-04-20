"""SMTP adapter edge cases (Story 5.3 — AC #8).

Covers the branches inside
:mod:`sentinel_prism.services.notifications.adapters.smtp` that the
orchestration tests mock out: address validation, successful send,
transport failure, auth failure, and the TLS / SSL mode selector.
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock

import pytest

from sentinel_prism.services.notifications.adapters import smtp as smtp_adapter


@pytest.mark.asyncio
async def test_smtp_success_uses_starttls_for_port_587(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeSMTP:
        def __init__(self, *, host: str, port: int, timeout: int) -> None:
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout

        def __enter__(self) -> "_FakeSMTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def ehlo(self) -> None:
            captured["ehlo_count"] = captured.get("ehlo_count", 0) + 1  # type: ignore[operator]

        def starttls(self) -> None:
            captured["starttls"] = True

        def login(self, user: str, password: str) -> None:
            captured["login"] = (user, password)

        def send_message(self, msg: object) -> None:
            captured["sent"] = msg

    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP", _FakeSMTP)

    ok, err_class, detail = await smtp_adapter.send_smtp_email(
        host="smtp.example",
        port=587,
        user="u",
        password="p",
        from_addr="from@test.local",
        to_addr="to@test.local",
        subject="s",
        body="b",
        use_tls=True,
    )
    assert ok is True
    assert err_class is None
    assert detail is None
    assert captured.get("starttls") is True
    assert captured.get("login") == ("u", "p")


@pytest.mark.asyncio
async def test_smtp_port_465_uses_implicit_ssl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ssl_used = {"count": 0}
    plain_used = {"count": 0}

    class _FakeSMTPSSL:
        def __init__(self, *, host: str, port: int, timeout: int) -> None:
            ssl_used["count"] += 1

        def __enter__(self) -> "_FakeSMTPSSL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def login(self, user: str, password: str) -> None:
            return None

        def send_message(self, msg: object) -> None:
            return None

    class _FakeSMTP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            plain_used["count"] += 1

    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP_SSL", _FakeSMTPSSL)
    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP", _FakeSMTP)

    ok, _err_class, _detail = await smtp_adapter.send_smtp_email(
        host="smtp.example",
        port=465,
        user=None,
        password=None,
        from_addr="from@test.local",
        to_addr="to@test.local",
        subject="s",
        body="b",
        use_tls=True,
    )
    assert ok is True
    assert ssl_used["count"] == 1
    assert plain_used["count"] == 0


@pytest.mark.asyncio
async def test_smtp_auth_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingSMTP:
        def __init__(self, **_kw: object) -> None:
            pass

        def __enter__(self) -> "_FailingSMTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, user: str, password: str) -> None:
            # Realistic smtplib echoes the offending AUTH command + base64 blob.
            raise smtplib.SMTPAuthenticationError(
                535,
                b"5.7.8 user=secret-operator AUTH LOGIN c2VjcmV0LW9wZXJhdG9yQGV4YW1wbGU=",
            )

        def send_message(self, msg: object) -> None:
            return None

    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP", _FailingSMTP)

    ok, err_class, detail = await smtp_adapter.send_smtp_email(
        host="smtp.example",
        port=587,
        user="secret-operator",
        password="pw",
        from_addr="from@test.local",
        to_addr="to@test.local",
        subject="s",
        body="b",
        use_tls=True,
    )
    assert ok is False
    assert err_class == "SMTPAuthenticationError"
    assert detail is not None
    # The configured user must be redacted.
    assert "secret-operator" not in detail
    # Long base64 blob must be redacted.
    assert "c2VjcmV0LW9wZXJhdG9yQGV4YW1wbGU=" not in detail
    assert "<redacted>" in detail or "<smtp-user-redacted>" in detail


@pytest.mark.asyncio
async def test_smtp_to_addr_validation_rejects_malformed_address() -> None:
    ok, err_class, detail = await smtp_adapter.send_smtp_email(
        host="smtp.example",
        port=587,
        user=None,
        password=None,
        from_addr="from@test.local",
        to_addr="victim@a, attacker@b",
        subject="s",
        body="b",
    )
    assert ok is False
    assert err_class == "AddressValidationError"
    assert detail is not None
    assert "to_addr" in detail


@pytest.mark.asyncio
async def test_smtp_from_addr_missing_at_sign_rejected() -> None:
    ok, err_class, _detail = await smtp_adapter.send_smtp_email(
        host="smtp.example",
        port=587,
        user=None,
        password=None,
        from_addr="not-an-email",
        to_addr="to@test.local",
        subject="s",
        body="b",
    )
    assert ok is False
    assert err_class == "AddressValidationError"


@pytest.mark.asyncio
async def test_smtp_connection_refused_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: object, **_kw: object) -> None:
        raise ConnectionRefusedError("connection refused")

    # SMTP(...) raises on construct → captured in the outer try.
    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP", MagicMock(side_effect=_raise))

    ok, err_class, detail = await smtp_adapter.send_smtp_email(
        host="smtp.example",
        port=587,
        user=None,
        password=None,
        from_addr="from@test.local",
        to_addr="to@test.local",
        subject="s",
        body="b",
    )
    assert ok is False
    assert err_class == "ConnectionRefusedError"
    assert detail is not None
    assert "connection refused" in detail.lower()
