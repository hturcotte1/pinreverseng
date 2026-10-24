"""Reporting surfaces: agent stats per job and the cross-job conversations view.

Mirrors documented Pin surfaces: the Reports / "AI Agent stats" panel, and the
All Conversations filters (Needs Attention / Interested / Not Interested / Unresponsive /
Archived). Pure reads over the store; no side effects.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from autopilot.models import ArchiveReason, Card, Direction, Interest, Stage
from autopilot.store import Store


@dataclass
class AgentStats:
    job_id: str
    total_cards: int = 0
    by_stage: dict[str, int] = field(default_factory=dict)
    sent_by_agent: int = 0
    drafts_waiting: int = 0
    meetings_booked: int = 0
    open_questions: int = 0
    nudges_sent: int = 0
    archived_by_reason: dict[str, int] = field(default_factory=dict)
    # Of cards that reached a decision, how many the agent handled without a human send.
    auto_handled: int = 0
    human_touched: int = 0

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id, "total_cards": self.total_cards, "by_stage": self.by_stage,
            "sent_by_agent": self.sent_by_agent, "drafts_waiting": self.drafts_waiting,
            "meetings_booked": self.meetings_booked, "open_questions": self.open_questions,
            "nudges_sent": self.nudges_sent, "archived_by_reason": self.archived_by_reason,
            "auto_handled": self.auto_handled, "human_touched": self.human_touched,
        }


def agent_stats(store: Store, job_id: str) -> AgentStats:
    from autopilot.models import OutboxStatus

    cards = store.cards_for_job(job_id)
    stats = AgentStats(job_id=job_id, total_cards=len(cards))
    stats.by_stage = dict(Counter(c.stage.value for c in cards))
    stats.archived_by_reason = dict(Counter(
        (c.archive_reason.value if c.archive_reason else "Unknown")
        for c in cards if c.stage is Stage.ARCHIVED))
    stats.nudges_sent = sum(c.nudges_sent for c in cards)
    stats.meetings_booked = sum(1 for c in cards if c.meeting is not None)
    card_ids = {c.id for c in cards}
    stats.open_questions = sum(1 for q in store.open_questions() if q.card_id in card_ids)
    for c in cards:
        thread = store.threads.get(c.thread_id, [])
        stats.sent_by_agent += sum(1 for m in thread if m.sent_by_agent)
        if c.last_human_outbound_at is not None:
            stats.human_touched += 1
        elif any(m.sent_by_agent for m in thread):
            stats.auto_handled += 1
    stats.drafts_waiting = sum(
        1 for o in store.outbox.values()
        if o.card_id in card_ids and o.status is OutboxStatus.DRAFT)
    return stats


# Cross-job conversations view -----------------------------------------------------------
CONVERSATION_FILTERS = ("needs_attention", "interested", "not_interested", "unresponsive", "archived")


def _needs_attention(store: Store, card: Card, now: datetime) -> bool:
    """Open question waiting, a draft to review, or an inbound with no reply yet."""
    from autopilot.models import OutboxStatus

    if any(q.card_id == card.id for q in store.open_questions()):
        return True
    if any(o.card_id == card.id and o.status is OutboxStatus.DRAFT for o in store.outbox.values()):
        return True
    thread = store.threads.get(card.thread_id, [])
    last = thread[-1] if thread else None
    return last is not None and last.direction is Direction.INBOUND and card.stage not in (
        Stage.ARCHIVED, Stage.HIRED)


def conversations(store: Store, filt: str, now: datetime, job_id: str | None = None) -> list[Card]:
    if filt not in CONVERSATION_FILTERS:
        raise ValueError(f"unknown filter {filt!r}; expected one of {CONVERSATION_FILTERS}")
    cards = [c for c in store.cards.values() if job_id is None or c.job_id == job_id]
    if filt == "needs_attention":
        picked = [c for c in cards if _needs_attention(store, c, now)]
    elif filt == "interested":
        picked = [c for c in cards if c.interest is Interest.INTERESTED and c.stage not in (
            Stage.ARCHIVED, Stage.HIRED)]
    elif filt == "not_interested":
        picked = [c for c in cards if c.archive_reason in (
            ArchiveReason.NOT_INTERESTED, ArchiveReason.UNSUBSCRIBED, ArchiveReason.OFFER_DECLINED)]
    elif filt == "unresponsive":
        picked = [c for c in cards if c.archive_reason is ArchiveReason.UNRESPONSIVE or (
            c.interest is Interest.NOT_NOW and c.stage not in (Stage.ARCHIVED, Stage.HIRED))]
    else:  # archived
        picked = [c for c in cards if c.stage is Stage.ARCHIVED]
    return sorted(picked, key=lambda c: c.last_inbound_at or now, reverse=True)
