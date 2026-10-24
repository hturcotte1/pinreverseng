"""Flight plan: a structured, inspectable record of every outbound decision.

Attached to each outbox item so a recruiter (or an auditor, or the autonomy governor) can see
exactly what the agent is about to do and why: the message, the knowledge-base facts it cites,
which side effects are reversible vs irreversible, whether it will auto-send or wait for review,
and the reasons behind that. This is the evidence trail that lets an org safely turn autonomy up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autopilot.brain.base import ComposeResult
from autopilot.models import Effect

REVERSIBLE = {"offered_slots", "link_sent"}
IRREVERSIBLE = {"book_meeting", "cancel_meeting"}


@dataclass
class FlightPlan:
    card_id: str
    kind: str
    body_preview: str
    cited_fact_ids: list[str] = field(default_factory=list)
    reversible_effects: list[str] = field(default_factory=list)
    irreversible_effects: list[str] = field(default_factory=list)
    auto_send: bool = False
    review_reasons: list[str] = field(default_factory=list)

    @property
    def has_irreversible(self) -> bool:
        return bool(self.irreversible_effects)

    def as_dict(self) -> dict:
        return {
            "card_id": self.card_id, "kind": self.kind, "body_preview": self.body_preview,
            "cited_facts": self.cited_fact_ids, "reversible": self.reversible_effects,
            "irreversible": self.irreversible_effects, "auto_send": self.auto_send,
            "review_reasons": self.review_reasons,
        }


def build(card_id: str, kind: str, body: str, result: ComposeResult | None,
          effects: list[Effect], auto_send: bool, review_reasons: list[str]) -> FlightPlan:
    cited = sorted({fid for c in (result.answers if result else []) for fid in c.fact_ids if fid != "recruiter"})
    return FlightPlan(
        card_id=card_id, kind=kind, body_preview=body.strip()[:280],
        cited_fact_ids=cited,
        reversible_effects=[e.kind for e in effects if e.kind in REVERSIBLE],
        irreversible_effects=[e.kind for e in effects if e.kind in IRREVERSIBLE],
        auto_send=auto_send, review_reasons=list(review_reasons),
    )
