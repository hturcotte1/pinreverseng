"""Automation settings: team-wide defaults with whole-object per-job overrides.

The option set mirrors the publicly documented "Outreach Automations" surface:
answer mode (drafts by default, no "off"), escalation channel, scheduling mode,
follow-up/archive rules and reply speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum


class AnswerMode(str, Enum):
    AUTO = "answer_automatically"
    DRAFT = "create_drafts"  # default


class EscalationChannel(str, Enum):
    SLACK = "slack"
    INBOX = "inbox"  # surface in-app only


class ScheduleMode(str, Enum):
    NONE = "do_not_schedule"
    LINK = "send_scheduling_link"
    BOOK = "schedule_meeting"


class ReplySpeed(str, Enum):
    NATURAL = "natural_pace"  # waits a few minutes, like a person would
    INSTANT = "near_instant"  # within seconds


# Weekday index (Mon=0) -> list of (start, end) local times the recruiter can meet.
Availability = dict[int, list[tuple[time, time]]]


def business_hours(start: time = time(9), end: time = time(17)) -> Availability:
    return {d: [(start, end)] for d in range(5)}


@dataclass
class SchedulingSettings:
    mode: ScheduleMode = ScheduleMode.BOOK
    scheduling_link: str | None = None
    availability: Availability = field(default_factory=business_hours)
    duration_minutes: int = 30
    buffer_minutes: int = 15
    attendees: list[str] = field(default_factory=list)
    skip_dates: set[date] = field(default_factory=set)
    slots_to_offer: int = 3
    min_notice_hours: int = 18
    horizon_days: int = 10
    slot_step_minutes: int = 30


@dataclass
class AutomationSettings:
    # Cadence defaults are grounded in Pin's published 2026 Recruiting Outreach Benchmark
    # (4M+ messages); see docs/RESEARCH_FINDINGS.md §4. Key figures: ~2 business days between
    # touches (48h silence -> next touch); 3 touches capture ~93% of replies; 93.9% of replies
    # land within 7 days. So: nudge once after ~2 business days, archive after ~10 days.
    answer_mode: AnswerMode = AnswerMode.DRAFT
    escalation: EscalationChannel = EscalationChannel.SLACK
    allow_learning: bool = True
    scheduling: SchedulingSettings = field(default_factory=SchedulingSettings)
    archive_not_interested: bool = True
    close_politely: bool = True  # short thank-you when archiving a "not interested"
    nudge_after_days: int | None = 3  # ~2 business days of silence (benchmark: 48h -> next touch)
    max_nudges: int = 1  # 3 touches already capture ~93% of replies; one reminder is enough
    archive_unresponsive_after_days: int | None = 10  # past the 7-day / 93.9% reply mass
    reply_speed: ReplySpeed = ReplySpeed.NATURAL
    # Guardrail knobs (our additions; not part of the documented surface).
    min_answer_confidence: float = 0.7
    max_agent_replies_per_day: int = 6
    human_takeover_hours: int = 48
    ai_disclosure_footer: str | None = None


def resolve_settings(team: AutomationSettings, job_override: AutomationSettings | None) -> AutomationSettings:
    """A per-job override replaces the team defaults wholesale for that job."""
    return job_override if job_override is not None else team
