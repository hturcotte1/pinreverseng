from datetime import datetime, timedelta, timezone

from autopilot.brain import HeuristicBrain, SchedulingIntent
from autopilot.brain.base import ConversationContext
from autopilot.brain.text import strip_quoted
from autopilot.knowledge import KnowledgeBase
from autopilot.models import Candidate, Company, Direction, Interest, Job, Message, OfferedSlot, Recruiter

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
SLOTS = [OfferedSlot(NOW + timedelta(days=d, hours=1), NOW + timedelta(days=d, hours=1, minutes=30)) for d in (1, 2, 3)]


def ctx(body: str, slots=None) -> ConversationContext:
    msg = Message("m", "t", Direction.INBOUND, "a@x.com", "r@x.com", "Re: hi", body, NOW)
    return ConversationContext(
        Recruiter("r", "Sam Rivera", "r@x.com"), Candidate("a", "Ana Tester", "a@x.com"), "Eng", "Acme", "",
        KnowledgeBase(Company("c", "Acme"), Job("j", "Eng", "c", "r")), [msg], msg, NOW, "America/New_York",
        offered_slots=slots or [])


brain = HeuristicBrain()


def test_strip_quoted_history():
    body = "Sounds good!\n\nOn Mon, Oct 26, 2026 at 9:00 AM Sam <s@x.com> wrote:\n> Hi Ana, interested?"
    assert strip_quoted(body) == "Sounds good!"


def test_interest_classes():
    assert brain.analyze(ctx("This sounds great, tell me more")).interest is Interest.INTERESTED
    assert brain.analyze(ctx("Not interested, thanks")).interest is Interest.NOT_INTERESTED
    assert brain.analyze(ctx("Not right now, maybe next year")).interest is Interest.NOT_NOW
    assert brain.analyze(ctx("Please unsubscribe me")).unsubscribe


def test_questions_exclude_scheduling_and_timing_asides():
    a = brain.analyze(ctx("Also, what's the base salary? Does Tuesday at 2pm work? Maybe reach out in March?"))
    assert a.questions == ["What's the base salary?"]


def test_slot_selection_by_ordinal_and_weekday():
    assert brain.analyze(ctx("The second one works", SLOTS)).accepted_slot_index == 1
    weekday = SLOTS[2].start.astimezone().strftime("%A")
    a = brain.analyze(ctx(f"{weekday} works for me", SLOTS))
    assert a.scheduling is SchedulingIntent.ACCEPTS_SLOT and a.accepted_slot_index == 2
    assert brain.analyze(ctx("None of those work, sorry", SLOTS)).scheduling is SchedulingIntent.SHARES_AVAILABILITY


def test_availability_windows_and_timezone():
    a = brain.analyze(ctx("I'm free Thursday after 3pm PT"))
    assert a.candidate_timezone == "America/Los_Angeles"
    [(start, end)] = a.proposed_windows
    assert start.hour == 15 and start.weekday() == 3 and end.hour == 18


def test_needs_human_on_negotiation():
    assert brain.analyze(ctx("I have a competing offer, can we negotiate?")).needs_human
