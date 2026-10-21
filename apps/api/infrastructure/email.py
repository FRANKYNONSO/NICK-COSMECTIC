"""Minimal email sender. No SMTP provider is configured anywhere in
this project yet (checked .env / .env.example -- nothing there), so
rather than block the whole forgot-password/email-verification
feature on picking and wiring up a provider, this fills in with a
local "dev outbox": every email that would have been sent gets
appended to dev_mail_outbox.log in the project root instead, so you
can open that file and grab the reset/verification link while
testing.

To send real email once you're ready: set SMTP_HOST (and optionally
SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS, SMTP_FROM_EMAIL)
in your .env -- send_email() will use smtplib automatically the
moment SMTP_HOST is present. Nothing else in the app needs to change.
"""
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger(__name__)

_DEV_OUTBOX_PATH = Path("dev_mail_outbox.log")


def _send_via_smtp(to_email: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() != "false"
    from_email = os.getenv("SMTP_FROM_EMAIL", username or "no-reply@nickcosmetics.local")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = to_email
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


def _write_dev_outbox(to_email: str, subject: str, body: str) -> None:
    entry = (
        f"\n{'=' * 60}\n"
        f"To: {to_email}\nSubject: {subject}\nSent: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"{body}\n"
    )
    with _DEV_OUTBOX_PATH.open("a", encoding="utf-8") as f:
        f.write(entry)
    logger.info("SMTP_HOST not configured -- wrote email to %s instead (to=%s, subject=%s)", _DEV_OUTBOX_PATH, to_email, subject)


def send_email(to_email: str, subject: str, body: str) -> None:
    if os.getenv("SMTP_HOST"):
        _send_via_smtp(to_email, subject, body)
    else:
        _write_dev_outbox(to_email, subject, body)
