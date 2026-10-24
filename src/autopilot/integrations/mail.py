"""Mailbox boundary.

Production adapters: Gmail (users.drafts.create / users.messages.send with threadId,
In-Reply-To and References headers) or Microsoft Graph (createReply / send). Drafts
are written into the sending account's own thread so a recruiter can review and send
them from their normal inbox.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Protocol


class MailProvider(Protocol):
    def send(self, thread_id: str, to: str, subject: str, body: str) -> str: ...

    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> str: ...

    def delete_draft(self, draft_id: str) -> None: ...


@dataclass
class SentMail:
    id: str
    thread_id: str
    to: str
    subject: str
    body: str


class InMemoryMailbox:
    def __init__(self) -> None:
        self.sent: list[SentMail] = []
        self.drafts: dict[str, SentMail] = {}
        self._ids = itertools.count(1)

    def send(self, thread_id: str, to: str, subject: str, body: str) -> str:
        mail = SentMail(f"msg_{next(self._ids)}", thread_id, to, subject, body)
        self.sent.append(mail)
        return mail.id

    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> str:
        draft = SentMail(f"draft_{next(self._ids)}", thread_id, to, subject, body)
        self.drafts[draft.id] = draft
        return draft.id

    def delete_draft(self, draft_id: str) -> None:
        self.drafts.pop(draft_id, None)
