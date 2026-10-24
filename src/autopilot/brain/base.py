"""The language-understanding boundary.

The agent splits each turn into two model calls with deterministic policy in between:

    analyze(context)        -> ReplyAnalysis   what did the candidate say / want?
    policy (engine.py)      -> ComposePlan     what should we do, given settings?
    compose(context, plan)  -> ComposeResult   write it, citing knowledge-base facts

Keeping the decision layer out of the model means settings like "drafts only" or
"never schedule" are enforced by code, and a prompt-injected email can at most
produce a bad *classification*, never an un-enumerated action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Protocol
from zoneinfo import ZoneInfo

from autopilot.knowledge import KnowledgeBase
from autopilot.models import Candidate, Interest, Meeting, Message, OfferedSlot, Recruiter

NOTE_SLOT_TAKEN = "The time the candidate picked is no longer available; apologise briefly and offer the new times."


class SchedulingIntent(str, Enum):
    NONE = "none"
    SHARES_AVAILABILITY = "shares_availability"
    ACCEPTS_SLOT = "accepts_offered_slot"
    RESCHEDULE = "requests_reschedule"
    CANCEL = "cancels"
    REQUESTS_LINK = "requests_link"


@dataclass
class ConversationContext:
    recruiter: Recruiter
    candidate: Candidate
    job_title: str
    company_display_name: str
    job_description: str
    kb: KnowledgeBase
    thread: list[Message]
    latest: Message | None
    now: datetime
    candidate_tz: str
    offered_slots: list[OfferedSlot] = field(default_factory=list)
    meeting: Meeting | None = None

    def local(self, when: datetime) -> datetime:
        return when.astimezone(ZoneInfo(self.candidate_tz))


@dataclass
class ReplyAnalysis:
    is_auto_reply: bool = False
    interest: Interest = Interest.UNCLEAR
    not_interested_reason: str | None = None
    questions: list[str] = field(default_factory=list)
    scheduling: SchedulingIntent = SchedulingIntent.NONE
    accepted_slot_index: int | None = None  # 0-based index into offered slots
    proposed_windows: list[tuple[datetime, datetime]] = field(default_factory=list)
    candidate_timezone: str | None = None
    offer_status: str = "none"  # none | accepted | declined
    unsubscribe: bool = False
    needs_human: bool = False
    needs_human_reason: str | None = None
    dealbreaker_conflicts: list[str] = field(default_factory=list)
    follow_up_on: date | None = None
    tone: str = "neutral"  # formal | neutral | casual
    summary: str = ""
    degraded: bool = False  # produced by a fallback path; engine will only draft
    # Candidate-experience signals (see autopilot.insights).
    objections: list[str] = field(default_factory=list)  # Objection codes raised in this message
    sentiment: str = "steady"  # warming | steady | cooling
    at_risk: bool = False  # likely to disengage or be lost without recruiter attention
    at_risk_reason: str | None = None


@dataclass
class ComposePlan:
    kind: str  # reply | nudge | follow_up_answer | closing | reengage
    questions: list[str] = field(default_factory=list)
    force_defer: set[str] = field(default_factory=set)
    recruiter_answers: list[tuple[str, str]] = field(default_factory=list)
    slot_labels: list[str] = field(default_factory=list)
    scheduling_link: str | None = None
    confirmed_meeting: str | None = None
    meeting_cancelled: bool = False
    follow_up_label: str | None = None
    notes: list[str] = field(default_factory=list)
    tone: str = "neutral"
    objections: list[str] = field(default_factory=list)  # soft concerns to acknowledge + answer from KB
    acknowledge: list[str] = field(default_factory=list)  # hard concerns: acknowledge, never answer/negotiate


@dataclass
class AnswerClaim:
    question: str
    answered: bool
    fact_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class ComposeResult:
    body: str
    answers: list[AnswerClaim] = field(default_factory=list)
    latest_activity: str = ""
    open_item: str | None = None
    degraded: bool = False


@dataclass
class DistilledFact:
    text: str | None
    scope: str = "job"  # job | company
    shareable: bool = True


class Brain(Protocol):
    def analyze(self, ctx: ConversationContext) -> ReplyAnalysis: ...

    def compose(self, ctx: ConversationContext, plan: ComposePlan) -> ComposeResult: ...

    def distill_fact(self, ctx: ConversationContext, question: str, answer: str) -> DistilledFact: ...
