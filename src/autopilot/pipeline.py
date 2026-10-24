"""Pipeline stage rules.

Automatic moves only go forward (Replied -> Qualifying -> Interviewing -> Hired); Archived
is reachable from anywhere. Two exceptions: a candidate archived for silence or "not
interested" who comes back interested is reopened, and nothing ever leaves Hired
automatically. A manual drag sets the new baseline; automation keeps moving forward from it.
"""

from __future__ import annotations

from autopilot.models import ArchiveReason, Card, Stage

ORDER = {Stage.REPLIED: 0, Stage.QUALIFYING: 1, Stage.INTERVIEWING: 2, Stage.HIRED: 3}
REOPENABLE = {ArchiveReason.UNRESPONSIVE, ArchiveReason.NOT_INTERESTED, ArchiveReason.OTHER}


def can_auto_transition(card: Card, target: Stage) -> bool:
    current = card.stage
    if current is target:
        return False
    if current is Stage.HIRED:
        return False
    if target is Stage.ARCHIVED:
        return True
    if current is Stage.ARCHIVED:
        return card.archive_reason in REOPENABLE and target in (Stage.QUALIFYING, Stage.INTERVIEWING)
    return ORDER[target] > ORDER[current]


def is_active(card: Card) -> bool:
    return card.stage not in (Stage.ARCHIVED, Stage.HIRED) and not card.paused
