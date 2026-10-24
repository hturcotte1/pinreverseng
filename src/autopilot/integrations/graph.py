"""Reference Microsoft Graph (Outlook / Microsoft 365) adapter for MailProvider.

Pure helpers (parse a Graph message resource into our Message; build the send payload)
are tested. The GraphMailbox class is a thin reference wrapper over an injected HTTP
caller, so it needs no SDK; a real deployment supplies a caller that adds the OAuth
bearer token and hits https://graph.microsoft.com/v1.0. Graph keeps a reply in-thread
via `conversationId` and the `createReply` endpoint, so we reply to the stored message id.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable

from autopilot.models import Direction, Message

# caller(method, path, json) -> dict. Supplied by the deployment (adds auth + base URL).
GraphCaller = Callable[[str, str, dict | None], dict]


def parse_graph_message(resource: dict, thread_id: str) -> Message:
    """Turn a Graph message resource into our Message.

    `bodyPreview` is plain text; `body.content` may be HTML, so strip tags if needed.
    """
    body = resource.get("body", {})
    content = body.get("content", "") or resource.get("bodyPreview", "")
    if body.get("contentType", "").lower() == "html":
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s+\n", "\n", content)
    received = resource.get("receivedDateTime", "")
    try:
        sent_at = datetime.fromisoformat(received.replace("Z", "+00:00")) if received \
            else datetime.now(timezone.utc)
    except ValueError:
        sent_at = datetime.now(timezone.utc)
    sender = resource.get("from", {}).get("emailAddress", {}).get("address", "")
    to = ", ".join(r.get("emailAddress", {}).get("address", "") for r in resource.get("toRecipients", []))
    headers = {h["name"]: h["value"] for h in resource.get("internetMessageHeaders", [])}
    headers.setdefault("Message-ID", resource.get("internetMessageId", ""))
    return Message(
        id=resource.get("id", ""),
        thread_id=thread_id,
        direction=Direction.INBOUND,
        sender=sender,
        to=to,
        subject=resource.get("subject", ""),
        body=content.strip(),
        sent_at=sent_at,
        headers=headers,
    )


def build_send_payload(to: str, subject: str, body: str) -> dict:
    return {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }


class GraphMailbox:
    """Thin MailProvider over Microsoft Graph. `resolve` maps our thread_id to a Graph
    message id to reply to (keeps the conversation threaded)."""

    def __init__(self, caller: GraphCaller, resolve: Callable[[str], str | None]):
        self.caller = caller
        self.resolve = resolve

    def send(self, thread_id: str, to: str, subject: str, body: str) -> str:
        reply_to = self.resolve(thread_id)
        if reply_to:  # keeps it in the Outlook conversation
            result = self.caller("POST", f"/me/messages/{reply_to}/reply",
                                 {"message": {"body": {"contentType": "Text", "content": body}}})
            return (result or {}).get("id", "sent")
        result = self.caller("POST", "/me/sendMail", build_send_payload(to, subject, body))
        return (result or {}).get("id", "sent")

    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> str:
        reply_to = self.resolve(thread_id)
        if reply_to:
            draft = self.caller("POST", f"/me/messages/{reply_to}/createReply", None)
            self.caller("PATCH", f"/me/messages/{draft['id']}",
                        {"body": {"contentType": "Text", "content": body}})
            return draft["id"]
        draft = self.caller("POST", "/me/messages", build_send_payload(to, subject, body)["message"])
        return draft["id"]

    def delete_draft(self, draft_id: str) -> None:
        self.caller("DELETE", f"/me/messages/{draft_id}", None)
