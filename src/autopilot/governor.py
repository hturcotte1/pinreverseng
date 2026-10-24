"""Autonomy governor: a per-job trust tier with a sticky safety circuit breaker.

Sending authority is not static. The governor reads live telemetry for a job — how often the
agent's messages needed human review, and whether candidates are unsubscribing — and sets a tier:

    AUTONOMOUS  send everything that passes the guardrails, bookings included
    ASSISTED    auto-send low-risk reactive answers, but draft anything with an irreversible effect
    DRAFT_ONLY  draft everything (the circuit breaker; sticky until a recruiter re-arms)

The baseline is trusting (configurable down to earn-up-from-zero). Bad signals only ever *demote*,
and demotion never silences a message — the floor is always a human-reviewed draft, so a
time-critical interview confirmation still goes out as a draft rather than vanishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from autopilot.flightplan import IRREVERSIBLE
from autopilot.models import ArchiveReason, Effect
from autopilot.settings import AutomationSettings
from autopilot.store import Store


class Tier(IntEnum):
    DRAFT_ONLY = 0
    ASSISTED = 1
    AUTONOMOUS = 2


@dataclass
class GovernorState:
    tier: Tier
    reasons: list[str] = field(default_factory=list)
    block_rate: float = 0.0
    decisions: int = 0
    unsubscribes: int = 0


def assess(store: Store, job_id: str, settings: AutomationSettings) -> GovernorState:
    if not settings.autonomy_governor:
        return GovernorState(Tier.AUTONOMOUS)
    if job_id in store.breaker_tripped:  # sticky — stays until rearm()
        return GovernorState(Tier.DRAFT_ONLY, [f"circuit breaker: {store.breaker_tripped[job_id]} — recruiter must re-arm"])

    cards = store.cards_for_job(job_id)
    card_ids = {c.id for c in cards}
    items = [o for o in store.outbox.values() if o.card_id in card_ids and o.flight_plan is not None]
    n = len(items)
    drafted = sum(1 for o in items if not o.flight_plan.auto_send)
    block_rate = drafted / n if n else 0.0
    unsub = sum(1 for c in cards if c.archive_reason is ArchiveReason.UNSUBSCRIBED) \
        - store.breaker_watermark.get(job_id, 0)  # forgive unsubscribes from before the last re-arm
    baseline = Tier(max(Tier.DRAFT_ONLY, min(settings.max_autonomy_tier, Tier.AUTONOMOUS)))

    tripped = (unsub >= settings.governor_unsub_trip
               or (n >= settings.governor_min_decisions and block_rate >= settings.governor_block_rate_draft))
    if tripped:
        why = (f"{unsub} unsubscribes" if unsub >= settings.governor_unsub_trip
               else f"{block_rate:.0%} of messages needed human review")
        store.breaker_tripped[job_id] = why
        return GovernorState(Tier.DRAFT_ONLY, [f"circuit breaker tripped: {why}"], block_rate, n, unsub)

    tier = baseline
    reasons: list[str] = []
    if (n >= settings.governor_min_decisions and block_rate >= settings.governor_block_rate_assist
            and baseline > Tier.ASSISTED):
        tier = Tier.ASSISTED
        reasons.append(f"elevated review rate ({block_rate:.0%}) — bookings need review")
    return GovernorState(tier, reasons, block_rate, n, unsub)


def governor_blockers(state: GovernorState, effects: list[Effect]) -> list[str]:
    """Extra auto-send blockers the tier imposes (never a silence — always resolves to a draft)."""
    if state.tier is Tier.DRAFT_ONLY:
        return [state.reasons[0] if state.reasons else "autonomy tier: draft-only"]
    if state.tier is Tier.ASSISTED and any(e.kind in IRREVERSIBLE for e in effects):
        return ["assisted tier: an irreversible action (book/cancel) needs review"]
    return []


def rearm(store: Store, job_id: str) -> bool:
    """Recruiter clears the circuit breaker for a job, forgiving signals up to now.

    Returns whether one was tripped. Sets a watermark so the same past unsubscribes don't
    immediately re-trip; a genuinely new unsubscribe after re-arm trips it again.
    """
    was = store.breaker_tripped.pop(job_id, None) is not None
    from autopilot.models import ArchiveReason
    store.breaker_watermark[job_id] = sum(
        1 for c in store.cards_for_job(job_id) if c.archive_reason is ArchiveReason.UNSUBSCRIBED)
    return was
