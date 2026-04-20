"""SMTP email via stdlib :mod:`smtplib` in a worker thread (Story 5.3).

Supports both opportunistic STARTTLS (submission port 587) and implicit
TLS (``SMTPS`` on port 465) so operators can point the sandbox at either
kind of provider. Exception text returned to the caller is sanitized:
``user``/``password`` and any auth-response bytes are scrubbed before
the string is persisted to ``notification_delivery_attempts.detail``
(AC #2 — "no raw secrets").
"""

from __future__ import annotations

import asyncio
import re
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr


_MAX_DETAIL = 500


class AddressValidationError(ValueError):
    """Raised for obviously-invalid ``From``/``To`` addresses before SMTP.

    ``email.utils.parseaddr`` does not itself reject every bad input
    (it silently returns ``('', '')`` for many cases). We reject inputs
    whose parsed address is empty, contains whitespace, or lacks an
    ``@`` — enough to stop accidental header-injection via a ``users.email``
    row that somehow picked up a comma or newline, without pretending to
    be a full RFC 5321 validator.
    """


def _validate_address(label: str, addr: str) -> str:
    if addr is None:
        raise AddressValidationError(f"{label} is missing")
    _, parsed = parseaddr(addr)
    if not parsed:
        raise AddressValidationError(f"{label} could not be parsed")
    if any(ch.isspace() for ch in parsed):
        raise AddressValidationError(f"{label} contains whitespace")
    if "," in parsed or ";" in parsed:
        raise AddressValidationError(f"{label} contains separator characters")
    if "@" not in parsed:
        raise AddressValidationError(f"{label} missing '@'")
    return parsed


def _sanitize_detail(text: str, *, user: str | None) -> str:
    """Strip obvious secret material from an SMTP exception string.

    ``smtplib`` exceptions frequently echo the failing command, which
    may include base64 ``AUTH`` payloads or the user identity. Replace
    the configured SMTP ``user`` verbatim, collapse base64-ish blobs
    after ``AUTH``, and drop CR/LF before clipping to ``_MAX_DETAIL``.
    """

    out = text.replace("\r", " ").replace("\n", " ")
    if user:
        out = out.replace(user, "<smtp-user-redacted>")
    out = re.sub(
        r"(?i)(AUTH\s+\S+)\s+\S+",
        r"\1 <redacted>",
        out,
    )
    out = re.sub(r"[A-Za-z0-9+/=]{32,}", "<redacted>", out)
    return out.strip()[:_MAX_DETAIL]


def _send_sync(
    *,
    host: str,
    port: int,
    user: str | None,
    password: str | None,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    use_tls: bool,
    use_ssl: bool,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    if use_ssl:
        # Implicit TLS — typically port 465. Do NOT also STARTTLS.
        with smtplib.SMTP_SSL(host=host, port=port, timeout=30) as smtp:
            smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(host=host, port=port, timeout=30) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls()
            smtp.ehlo()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


async def send_smtp_email(
    *,
    host: str,
    port: int,
    user: str | None,
    password: str | None,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    use_tls: bool = True,
    use_ssl: bool | None = None,
) -> tuple[bool, str | None, str | None]:
    """Send one message; returns ``(ok, error_class, error_detail)``.

    ``use_ssl`` defaults to ``None`` which means "auto-select by port":
    port 465 implies implicit TLS (``SMTP_SSL``); everything else uses
    plaintext-then-``STARTTLS`` gated by ``use_tls``. Pass ``use_ssl``
    explicitly to force one mode regardless of port.

    ``error_detail`` has been stripped of the configured ``user`` and of
    common auth-echo blobs before return; callers still cap length via
    their own ``_safe_detail`` utility.
    """

    try:
        from_parsed = _validate_address("from_addr", from_addr)
        to_parsed = _validate_address("to_addr", to_addr)
    except AddressValidationError as exc:
        return False, "AddressValidationError", str(exc)[:_MAX_DETAIL]

    ssl_mode = use_ssl if use_ssl is not None else (port == 465)

    try:
        await asyncio.to_thread(
            _send_sync,
            host=host,
            port=port,
            user=user,
            password=password,
            from_addr=from_parsed,
            to_addr=to_parsed,
            subject=subject,
            body=body,
            use_tls=use_tls,
            use_ssl=ssl_mode,
        )
    except Exception as exc:  # noqa: BLE001 — surface transport failures to log/DB
        return (
            False,
            type(exc).__name__,
            _sanitize_detail(str(exc), user=user),
        )
    return True, None, None
