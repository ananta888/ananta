"""Outbound email port and SMTP adapter for Hub account verification."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

from agent.config import settings


class RegistrationEmailSender(Protocol):
    def send_verification(self, *, recipient: str, username: str, verification_url: str) -> None: ...


class SmtpRegistrationEmailSender:
    def send_verification(self, *, recipient: str, username: str, verification_url: str) -> None:
        if not settings.hub_registration_smtp_host or not settings.hub_registration_smtp_from:
            raise RuntimeError("registration_smtp_not_configured")
        message = EmailMessage()
        message["Subject"] = "Ananta: E-Mail-Adresse bestätigen"
        message["From"] = settings.hub_registration_smtp_from
        message["To"] = recipient
        message.set_content(
            f"Hallo {username},\n\nbitte bestätige deine E-Mail-Adresse:\n{verification_url}\n\n"
            "Falls du dieses Konto nicht angelegt hast, ignoriere diese Nachricht."
        )
        with smtplib.SMTP(settings.hub_registration_smtp_host, settings.hub_registration_smtp_port, timeout=15) as smtp:
            if settings.hub_registration_smtp_starttls:
                smtp.starttls()
            if settings.hub_registration_smtp_username:
                smtp.login(settings.hub_registration_smtp_username, settings.hub_registration_smtp_password)
            smtp.send_message(message)


def get_registration_email_sender() -> RegistrationEmailSender:
    return SmtpRegistrationEmailSender()
