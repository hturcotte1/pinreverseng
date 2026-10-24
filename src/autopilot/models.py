"""Core domain objects: jobs, candidates, threads, pipeline cards, open questions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Stage(str, Enum):
    REPLIED = "Replied"
    QUALIFYING = "Qualifying"
    INTERVIEWING = "Interviewing"
    ARCHIVED = "Archived"
    HIRED = "Hired"


class InterviewStatus(str, Enum):
    NOT_SCHEDULED = "Not scheduled"
    SCHEDULED = "Scheduled"
    INTERVIEWED = "Interviewed"


class ArchiveReason(str, Enum):
    NOT_INTERESTED = "Not Interested"
    UNRESPONSIVE = "Unresponsive"
    UNSUBSCRIBED = "Unsubscribed"
    OFFER_DECLINED = "Declined Offer"
    REJECTED = "Rejected"
    PREFERRED_ANOTHER = "Preferred Another Candidate"
    OTHER = "Other"


class Direction(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Interest(str, Enum):
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    NOT_NOW = "not_now"
    UNCLEAR = "unclear"


@dataclass
class Company:
    id: str
    name: str
    hide_details: bool = False  # confidential client search: never name the company
    knowledge: str = ""  # company-wide facts, shared by every job at this company


@dataclass
class Recruiter:
    id: str
    name: str
    email: str
    timezone: str = "America/New_York"
    slack_user_id: str | None = None
    style: object | None = None  # autopilot.voice.StyleProfile, calibrated from sent messages

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


@dataclass
class Job:
    id: str
    title: str
    company_id: str
    owner_id: str
    description: str = ""
    knowledge: str = ""  # job-level knowledge base text
    settings_override: "AutomationSettings | None" = None  # noqa: F821 - resolved in settings.py


@dataclass
class Candidate:
    id: str
    name: str
    email: str
    timezone: str | None = None
    current_company: str | None = None

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


@dataclass
class Message:
    id: str
    thread_id: str
    direction: Direction
    sender: str
    to: str
    subject: str
    body: str
    sent_at: datetime
    headers: dict[str, str] = field(default_factory=dict)
    sent_by_agent: bool = False
    from_sequence: bool = False  # part of the automated outreach sequence


@dataclass
class Meeting:
    start: datetime
    end: datetime
    event_id: str


@dataclass
class OfferedSlot:
    start: datetime
    end: datetime


@dataclass
class Card:
    """One candidate's position in one job's pipeline (the board card)."""

    id: str
    candidate_id: str
    job_id: str
    thread_id: str
    stage: Stage = Stage.REPLIED
    interview_status: InterviewStatus | None = None
    archive_reason: ArchiveReason | None = None
    interest: Interest = Interest.UNCLEAR
    latest_activity: str = ""  # one-line summary of the most recent step
    open_item: str | None = None  # something still pending
    meeting: Meeting | None = None
    offered_slots: list[OfferedSlot] = field(default_factory=list)
    scheduling_link_sent: bool = False
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    last_human_outbound_at: datetime | None = None
    nudges_sent: int = 0
    follow_up_on: datetime | None = None  # "not now, reach out in March"
    manually_moved_at: datetime | None = None
    paused: bool = False  # recruiter took the conversation over
    candidate_timezone: str | None = None
    sentiment: str = "steady"  # warming | steady | cooling (latest read)
    objections: list[str] = field(default_factory=list)  # distinct objection codes seen on this card
    at_risk: bool = False
    at_risk_reason: str | None = None
    at_risk_alerted: bool = False  # dedup the proactive recruiter heads-up
    recovery_attempted: bool = False  # at most one soft-no recovery attempt per card
    proactive_touches: int = 0  # agent-initiated sends (nudge/re-engage/lifecycle), budget-capped
    last_proactive_at: datetime | None = None
    prep_sent: bool = False  # interview prep note sent (reset if the meeting is cancelled/rescheduled)
    confirm_sent: bool = False  # day-of confirmation sent


@dataclass
class OpenQuestion:
    id: str
    card_id: str
    question: str
    asked_at: datetime
    answered_at: datetime | None = None
    answer: str | None = None
    notifier_ref: str | None = None  # e.g. Slack channel:thread_ts

    @property
    def is_open(self) -> bool:
        return self.answered_at is None


@dataclass
class InboxEvent:
    """Activity rows shown alongside the email thread."""

    card_id: str
    at: datetime
    text: str


@dataclass
class AuditEntry:
    """Every decision, with the reasoning that produced it."""

    at: datetime
    card_id: str | None
    action: str
    detail: str
    reasoning: str = ""


class OutboxStatus(str, Enum):
    SCHEDULED = "scheduled"  # agent will send at send_at (reply-speed delay)
    DRAFT = "draft"  # waiting in the recruiter's mailbox for review
    SENT = "sent"
    CANCELLED = "cancelled"


@dataclass
class Effect:
    """A side effect that must only happen once the message actually goes out."""

    kind: str  # book_meeting | cancel_meeting | link_sent | offered_slots
    slot: OfferedSlot | None = None
    slots: list[OfferedSlot] = field(default_factory=list)


@dataclass
class OutboxItem:
    id: str
    card_id: str
    thread_id: str
    to: str
    subject: str
    body: str
    kind: str
    created_at: datetime
    status: OutboxStatus
    send_at: datetime | None = None
    draft_ref: str | None = None
    effects: list[Effect] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    latest_activity: str = ""
    open_item: str | None = None
