"""Shadow mode scoring: compare what the agent WOULD have done to what the recruiter did.

Run an Autopilot with shadow=True over a throwaway store seeded from real history; it records a
FlightPlan per decision and delivers nothing. Then label each decision and compare it to the
recruiter's actual action to get an agreement rate — the evidence an org uses to decide whether to
turn autonomy up, and the signal the governor can fold in. Pure functions; no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass

from autopilot.flightplan import FlightPlan, IRREVERSIBLE

# Coarse action labels, from most to least committing.
ACTIONS = ("book", "schedule", "escalate", "answer", "close", "nudge", "other")


def label(kind: str, irreversible_effects: list[str], reversible_effects: list[str], auto_send: bool) -> str:
    if any(e == "book_meeting" for e in irreversible_effects):
        return "book"
    if reversible_effects or kind in ("confirm", "prep"):
        return "schedule"
    if not auto_send:
        return "escalate"  # drafted for a human
    if kind == "closing":
        return "close"
    if kind == "nudge":
        return "nudge"
    if kind in ("reply", "follow_up_answer", "rescue", "reengage"):
        return "answer"
    return "other"


def label_flightplan(fp: FlightPlan) -> str:
    return label(fp.kind, fp.irreversible_effects, fp.reversible_effects, fp.auto_send)


@dataclass
class Agreement:
    n: int = 0
    agreed: int = 0
    by_action: dict[str, tuple[int, int]] = None  # action -> (agreed, total)

    @property
    def rate(self) -> float:
        return self.agreed / self.n if self.n else 0.0


def score(pairs: list[tuple[str, str]]) -> Agreement:
    """pairs: (agent_label, recruiter_label). Returns overall and per-action agreement."""
    by: dict[str, list[int]] = {}
    agreed = 0
    for proposed, actual in pairs:
        ok = proposed == actual
        agreed += ok
        slot = by.setdefault(actual, [0, 0])
        slot[0] += ok
        slot[1] += 1
    return Agreement(n=len(pairs), agreed=agreed, by_action={k: (v[0], v[1]) for k, v in by.items()})
