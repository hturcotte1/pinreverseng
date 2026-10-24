"""In-memory repository. Swap for a database in production; the engine only uses these methods."""

from __future__ import annotations

import itertools
from datetime import datetime

from autopilot.knowledge import LearnedFacts
from autopilot.models import (
    AuditEntry,
    Candidate,
    Card,
    Company,
    InboxEvent,
    Job,
    Message,
    OpenQuestion,
    OutboxItem,
    OutboxStatus,
    Recruiter,
)


class Store:
    def __init__(self) -> None:
        self.companies: dict[str, Company] = {}
        self.recruiters: dict[str, Recruiter] = {}
        self.jobs: dict[str, Job] = {}
        self.candidates: dict[str, Candidate] = {}
        self.cards: dict[str, Card] = {}
        self.threads: dict[str, list[Message]] = {}
        self.thread_owner: dict[str, tuple[str, str]] = {}  # thread -> (candidate_id, job_id)
        self.questions: dict[str, OpenQuestion] = {}
        self.outbox: dict[str, OutboxItem] = {}
        self.events: list[InboxEvent] = []
        self.audit: list[AuditEntry] = []
        self.suppressed: set[str] = set()  # unsubscribed emails, honoured on every channel
        self.responded: set[str] = set()  # candidate ids whose sequences are halted team-wide
        self.breaker_tripped: dict[str, str] = {}  # job_id -> reason; sticky until a recruiter re-arms
        self.breaker_watermark: dict[str, int] = {}  # job_id -> unsubscribes forgiven at last re-arm
        self.learned = LearnedFacts()
        self._ids = itertools.count(1)

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids)}"

    def add(self, obj: Company | Recruiter | Job | Candidate) -> None:
        bucket = {Company: self.companies, Recruiter: self.recruiters, Job: self.jobs,
                  Candidate: self.candidates}[type(obj)]
        bucket[obj.id] = obj

    def card_for_thread(self, thread_id: str) -> Card | None:
        return next((c for c in self.cards.values() if c.thread_id == thread_id), None)

    def cards_for_job(self, job_id: str) -> list[Card]:
        return [c for c in self.cards.values() if c.job_id == job_id]

    def pending_outbox(self, card_id: str) -> list[OutboxItem]:
        return [o for o in self.outbox.values()
                if o.card_id == card_id and o.status in (OutboxStatus.SCHEDULED, OutboxStatus.DRAFT)]

    def open_questions(self, card_id: str | None = None) -> list[OpenQuestion]:
        return [q for q in self.questions.values() if q.is_open and (card_id is None or q.card_id == card_id)]

    def agent_sends_since(self, thread_id: str, since: datetime) -> int:
        return sum(1 for m in self.threads.get(thread_id, []) if m.sent_by_agent and m.sent_at >= since)
