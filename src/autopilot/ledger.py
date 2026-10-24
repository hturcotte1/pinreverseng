"""Conversion ledger: reconstruct the funnel and attribute every lost candidate.

Pin reports vanity agent stats. This reconstructs the real funnel from the audit log —
Replied → Qualifying → Interviewing → Booked → Interviewed → Hired — with step-to-step
conversion, average dwell time per stage, and a named leak reason and leak stage for every
candidate who dropped out. It is the measurement substrate the Flight Plan, the Autonomy
Governor, and any future optimizer close their loops on. Pure reads; no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from autopilot.models import InterviewStatus, Stage
from autopilot.pipeline import ORDER
from autopilot.store import Store

# Funnel steps in order. The first four are pipeline stages; the last three are milestones.
STEPS = ["replied", "qualifying", "interviewing", "booked", "interviewed", "hired"]


@dataclass
class Leak:
    card_id: str
    candidate: str
    stage_before: str
    reason: str


@dataclass
class Funnel:
    counts: dict[str, int] = field(default_factory=dict)
    conversion: dict[str, float] = field(default_factory=dict)      # step -> fraction of 'replied'
    step_conversion: dict[str, float] = field(default_factory=dict)  # step -> fraction of previous step
    dwell_hours: dict[str, float] = field(default_factory=dict)     # transition -> avg hours
    leaks: list[Leak] = field(default_factory=list)
    n: int = 0

    def as_dict(self) -> dict:
        from collections import Counter
        return {
            "n": self.n, "counts": self.counts,
            "conversion_vs_replied": {k: round(v, 3) for k, v in self.conversion.items()},
            "step_conversion": {k: round(v, 3) for k, v in self.step_conversion.items()},
            "avg_dwell_hours": {k: round(v, 1) for k, v in self.dwell_hours.items()},
            "leaks_by_reason": dict(Counter(leak.reason for leak in self.leaks)),
        }


def _entered(store: Store, card_id: str) -> dict[str, datetime]:
    """First time the card entered each stage, from the audit trail."""
    entered: dict[str, datetime] = {}
    for e in store.audit:
        if e.card_id != card_id:
            continue
        if e.action == "open":
            entered.setdefault(Stage.REPLIED.value, e.at)
        elif e.action in ("stage", "manual_move") and " -> " in e.detail:
            entered.setdefault(e.detail.split(" -> ", 1)[1], e.at)
    return entered


def _stage_before_archive(store: Store, card_id: str) -> str:
    before = Stage.REPLIED.value
    for e in store.audit:
        if e.card_id == card_id and e.action in ("stage", "manual_move") and " -> " in e.detail:
            left, right = e.detail.split(" -> ", 1)
            if right == Stage.ARCHIVED.value:
                return left
            before = right
    return before


def _furthest_order(store: Store, card, entered: dict[str, datetime]) -> int:
    orders = [ORDER[s] for s in entered if s in ORDER]
    if card.stage is Stage.ARCHIVED:
        prior = _stage_before_archive(store, card.id)
        if prior in ORDER:
            orders.append(ORDER[prior])
    elif card.stage in ORDER:
        orders.append(ORDER[card.stage])
    if card.meeting is not None or card.interview_status in (InterviewStatus.SCHEDULED, InterviewStatus.INTERVIEWED):
        orders.append(ORDER[Stage.INTERVIEWING])
    return max(orders, default=0)


def funnel(store: Store, now: datetime, job_id: str | None = None) -> Funnel:
    cards = [c for c in store.cards.values() if job_id is None or c.job_id == job_id]
    f = Funnel(n=len(cards))
    counts = dict.fromkeys(STEPS, 0)
    dwell: dict[str, list[float]] = {}

    for card in cards:
        entered = _entered(store, card.id)
        furthest = _furthest_order(store, card, entered)
        counts["replied"] += 1
        if furthest >= ORDER[Stage.QUALIFYING]:
            counts["qualifying"] += 1
        if furthest >= ORDER[Stage.INTERVIEWING]:
            counts["interviewing"] += 1
        if card.meeting is not None or card.interview_status in (InterviewStatus.SCHEDULED,
                                                                 InterviewStatus.INTERVIEWED):
            counts["booked"] += 1
        if card.interview_status is InterviewStatus.INTERVIEWED:
            counts["interviewed"] += 1
        if card.stage is Stage.HIRED:
            counts["hired"] += 1

        for a, b in (("Replied", "Qualifying"), ("Qualifying", "Interviewing")):
            if a in entered and b in entered and entered[b] >= entered[a]:
                dwell.setdefault(f"{a}->{b}", []).append((entered[b] - entered[a]).total_seconds() / 3600)

        if card.stage is Stage.ARCHIVED:
            f.leaks.append(Leak(card.id, store.candidates[card.candidate_id].name,
                                _stage_before_archive(store, card.id),
                                card.archive_reason.value if card.archive_reason else "Unknown"))

    f.counts = counts
    base = counts["replied"] or 1
    f.conversion = {s: counts[s] / base for s in STEPS}
    for i in range(1, len(STEPS)):
        prev = counts[STEPS[i - 1]] or 1
        f.step_conversion[f"{STEPS[i - 1]}->{STEPS[i]}"] = counts[STEPS[i]] / prev
    f.dwell_hours = {k: sum(v) / len(v) for k, v in dwell.items() if v}
    return f


def summary_line(f: Funnel) -> str:
    c = f.counts
    return (f"replied {c['replied']} → qualifying {c['qualifying']} → interviewing {c['interviewing']} "
            f"→ booked {c['booked']} → interviewed {c['interviewed']} → hired {c['hired']}")
