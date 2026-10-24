from autopilot.models import ArchiveReason, Card, Stage
from autopilot.pipeline import can_auto_transition


def card(stage: Stage, reason: ArchiveReason | None = None) -> Card:
    return Card("c", "cand", "job", "thr", stage=stage, archive_reason=reason)


def test_forward_only():
    assert can_auto_transition(card(Stage.REPLIED), Stage.QUALIFYING)
    assert can_auto_transition(card(Stage.QUALIFYING), Stage.INTERVIEWING)
    assert not can_auto_transition(card(Stage.INTERVIEWING), Stage.QUALIFYING)


def test_archive_reachable_but_hired_is_terminal():
    assert can_auto_transition(card(Stage.INTERVIEWING), Stage.ARCHIVED)
    assert not can_auto_transition(card(Stage.HIRED), Stage.ARCHIVED)


def test_reopen_only_for_soft_archive_reasons():
    assert can_auto_transition(card(Stage.ARCHIVED, ArchiveReason.UNRESPONSIVE), Stage.QUALIFYING)
    assert not can_auto_transition(card(Stage.ARCHIVED, ArchiveReason.UNSUBSCRIBED), Stage.QUALIFYING)
    assert not can_auto_transition(card(Stage.ARCHIVED, ArchiveReason.REJECTED), Stage.QUALIFYING)
