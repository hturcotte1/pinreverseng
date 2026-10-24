"""Plain-text rendering of a job's pipeline board and a card's inbox timeline."""

from __future__ import annotations

from datetime import datetime

from autopilot.models import Card, InterviewStatus, Stage
from autopilot.scheduling import format_slot
from autopilot.store import Store

COLUMNS = [Stage.REPLIED, Stage.QUALIFYING, Stage.INTERVIEWING, Stage.ARCHIVED, Stage.HIRED]


def _countdown(now: datetime, when: datetime) -> str:
    days = (when.date() - now.date()).days
    if days > 1:
        return f"in {days} days"
    return "tomorrow" if days == 1 else "today" if days == 0 else "past"


def render_card(store: Store, card: Card, now: datetime) -> list[str]:
    candidate = store.candidates[card.candidate_id]
    tz = store.recruiters[store.jobs[card.job_id].owner_id].timezone
    lines = [f"  {candidate.name}"]
    if card.stage is Stage.INTERVIEWING and card.interview_status:
        status = card.interview_status.value
        if card.meeting and card.interview_status is InterviewStatus.SCHEDULED:
            status = f"{format_slot(card.meeting.start, tz)} - {_countdown(now, card.meeting.start)}"
        lines.append(f"    [{status}]")
    if card.latest_activity:
        lines.append(f"    * {card.latest_activity}")
    if card.open_item:
        lines.append(f"    > {card.open_item}")
    if card.stage is Stage.ARCHIVED and card.archive_reason:
        lines.append(f"    reason: {card.archive_reason.value}")
    return lines


def render_board(store: Store, job_id: str, now: datetime) -> str:
    job = store.jobs[job_id]
    out = [f"=== Pipeline: {job.title} ==="]
    for stage in COLUMNS:
        cards = [c for c in store.cards_for_job(job_id) if c.stage is stage]
        out.append(f"\n{stage.value} ({len(cards)})")
        for card in sorted(cards, key=lambda c: c.last_inbound_at or now, reverse=True):
            out.extend(render_card(store, card, now))
    return "\n".join(out)


def render_timeline(store: Store, card: Card) -> str:
    rows: list[tuple[datetime, str]] = []
    for m in store.threads.get(card.thread_id, []):
        who = "agent" if m.sent_by_agent else ("recruiter" if m.direction.value == "outbound" else "candidate")
        tag = " [Sent by agent]" if m.sent_by_agent else ""
        rows.append((m.sent_at, f"{who}{tag}: {m.body.strip().splitlines()[0][:90] if m.body.strip() else ''}"))
    rows.extend((e.at, f"-- {e.text}") for e in store.events if e.card_id == card.id)
    rows.sort(key=lambda r: r[0])
    return "\n".join(f"{at:%b %d %H:%M} {text}" for at, text in rows)
