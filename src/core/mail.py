"""Configured email delivery with explicit per-message event ownership."""
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import BadHeaderError, EmailMessage, get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.db import transaction
from django.utils.module_loading import import_string

import audit.services
from core.models import OutboxMessage
from events.models import Event


class EmailDeliveryError(Exception):
    """The configured mail service did not accept a message."""


class OutboxBackend(BaseEmailBackend):
    """Persist plaintext messages for private organizer delivery, with no SMTP."""

    def send_messages(self, email_messages) -> int:
        count = 0
        with transaction.atomic():
            for message in email_messages or []:
                recipients = message.recipients()
                if not recipients:
                    continue
                row = OutboxMessage.objects.create(
                    event=getattr(message, "verdict_event", None), recipients=recipients,
                    subject=message.subject, body=message.body, from_email=message.from_email,
                )
                audit.services.record(
                    None, "mail.queued", event=row.event, target=row,
                    summary="A message was queued for private organizer delivery.",
                    data={"recipient_count": len(recipients)},
                )
                count += 1
        return count


def uses_outbox() -> bool:
    return issubclass(import_string(settings.EMAIL_BACKEND), OutboxBackend)


def send_event_mail(*, event: Event | None, subject: str, message: str,
                    recipient_list: list[str]) -> str:
    """Return queued or sent only after the configured backend accepts a message."""
    connection = get_connection(fail_silently=False)
    email = EmailMessage(
        subject=subject, body=message, from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipient_list, connection=connection,
    )
    email.verdict_event = event
    try:
        accepted = connection.send_messages([email])
    except (OSError, SMTPException, BadHeaderError) as exc:
        raise EmailDeliveryError("Email delivery is unavailable.") from exc
    if accepted != 1:
        raise EmailDeliveryError("The mail service did not accept the message.")
    return "queued" if isinstance(connection, OutboxBackend) else "sent"
