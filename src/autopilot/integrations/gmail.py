"""Reference Gmail adapter for MailProvider.

The pure helpers (MIME building with in-thread reply headers, and parsing a Gmail API
message resource into our Message) are unit-tested. The GmailMailbox class is a thin,
untested-here wrapper over the Gmail REST client; it needs OAuth credentials and the
`google-api-python-client` package (guarded import), neither available in this sandbox.

Threading: replying in the same conversation needs the Gmail `threadId` plus RFC 2822
`In-Reply-To`/`References` headers pointing at the candidate's last `Message-ID`. The
adapter keeps that mapping in a `ThreadResolver` so `send(thread_id, ...)` can resolve it.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Protocol

from autopilot.models import Direction, Message


@dataclass
class ThreadRef:
    provider_thread_id: str
    last_message_id: str | None = None  # RFC 2822 Message-ID of the latest message
    references: list[str] = field(default_factory=list)


class ThreadResolver(Protocol):
    def resolve(self, thread_id: str) -> ThreadRef | None: ...


def build_mime(from_addr: str, to_addr: str, subject: str, body: str,
               in_reply_to: str | None = None, references: list[str] | None = None) -> EmailMessage:
    """RFC 2822 message with threading headers so Gmail/Outlook keep it in one conversation."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        refs = list(references or [])
        if in_reply_to not in refs:
            refs.append(in_reply_to)
        msg["References"] = " ".join(refs)
    return msg


def gmail_raw(message: EmailMessage) -> str:
    """base64url-encoded message for Gmail `messages.send` / `drafts.create`."""
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def _header(payload_headers: list[dict], name: str) -> str:
    return next((h["value"] for h in payload_headers if h["name"].lower() == name.lower()), "")


def _walk_parts(payload: dict) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    for part in payload.get("parts", []):
        text = _walk_parts(part)
        if text:
            return text
    if mime == "text/html" and data:
        html = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def parse_gmail_message(resource: dict, thread_id: str) -> Message:
    """Turn a Gmail `users.messages.get` resource (format=full) into our Message."""
    payload = resource.get("payload", {})
    headers = payload.get("headers", [])
    sent_raw = _header(headers, "Date")
    try:
        sent_at = parsedate_to_datetime(sent_raw) if sent_raw else datetime.now(timezone.utc)
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        sent_at = datetime.now(timezone.utc)
    sender = _header(headers, "From")
    email_match = re.search(r"<([^>]+)>", sender)
    return Message(
        id=resource.get("id", ""),
        thread_id=thread_id,
        direction=Direction.INBOUND,
        sender=email_match.group(1) if email_match else sender.strip(),
        to=_header(headers, "To"),
        subject=_header(headers, "Subject"),
        body=_walk_parts(payload).strip(),
        sent_at=sent_at,
        headers={"Message-ID": _header(headers, "Message-ID"),
                 "Auto-Submitted": _header(headers, "Auto-Submitted"),
                 "Precedence": _header(headers, "Precedence")},
    )


class GmailMailbox:  # pragma: no cover - needs OAuth + google-api-python-client
    """Thin MailProvider over the Gmail REST API. Reference only."""

    def __init__(self, service, sender: str, resolver: ThreadResolver):
        self.service = service  # googleapiclient.discovery.build("gmail", "v1", ...)
        self.sender = sender
        self.resolver = resolver

    def _raw(self, thread_id: str, to: str, subject: str, body: str) -> tuple[str, str | None]:
        ref = self.resolver.resolve(thread_id)
        mime = build_mime(self.sender, to, subject, body,
                          in_reply_to=ref.last_message_id if ref else None,
                          references=ref.references if ref else None)
        return gmail_raw(mime), (ref.provider_thread_id if ref else None)

    def send(self, thread_id: str, to: str, subject: str, body: str) -> str:
        raw, gthread = self._raw(thread_id, to, subject, body)
        payload = {"raw": raw} | ({"threadId": gthread} if gthread else {})
        return self.service.users().messages().send(userId="me", body=payload).execute()["id"]

    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> str:
        raw, gthread = self._raw(thread_id, to, subject, body)
        message = {"raw": raw} | ({"threadId": gthread} if gthread else {})
        return self.service.users().drafts().create(userId="me", body={"message": message}).execute()["id"]

    def delete_draft(self, draft_id: str) -> None:
        self.service.users().drafts().delete(userId="me", id=draft_id).execute()
