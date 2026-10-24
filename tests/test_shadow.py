import random

from autopilot.brain import HeuristicBrain
from autopilot.engine import Autopilot
from autopilot.integrations.calendar import InMemoryCalendar
from autopilot.integrations.mail import InMemoryMailbox
from autopilot.integrations.slack import RecordingNotifier
from autopilot.models import Direction, Message, OutboxStatus
from autopilot.shadow import label, label_flightplan, score
from conftest import make_world


def test_label_actions():
    assert label("reply", ["book_meeting"], [], True) == "book"
    assert label("reply", [], ["offered_slots"], True) == "schedule"
    assert label("confirm", [], [], True) == "schedule"
    assert label("reply", [], [], False) == "escalate"     # drafted for a human
    assert label("closing", [], [], True) == "close"
    assert label("reply", [], [], True) == "answer"


def test_shadow_mode_delivers_nothing_but_records_decisions():
    base = make_world()                 # borrow its fully-wired store/clock
    mail = InMemoryMailbox()
    agent = Autopilot(base.store, HeuristicBrain(), mail, InMemoryCalendar(), RecordingNotifier(),
                      base.clock, base.agent.team_settings, random.Random(1), shadow=True)
    msg = Message(base.store.new_id("m"), base.threads["ana"], Direction.INBOUND, "ana@example.com",
                  "sam@acme.com", "Re: Platform Engineer", "Interested! What's the salary range?",
                  base.clock.now())
    agent.handle_inbound(msg)
    base.clock.advance(minutes=15)
    agent.tick()
    items = list(base.store.outbox.values())
    assert items and all(o.status is OutboxStatus.SHADOW for o in items)
    assert mail.sent == [] and mail.drafts == {}        # nothing delivered
    assert all(o.flight_plan is not None for o in items)
    assert label_flightplan(items[-1].flight_plan) in ("answer", "schedule", "escalate")


def test_score_agreement():
    pairs = [("answer", "answer"), ("book", "book"), ("answer", "escalate"), ("nudge", "nudge")]
    ag = score(pairs)
    assert ag.n == 4 and ag.agreed == 3 and abs(ag.rate - 0.75) < 1e-9
    assert ag.by_action["escalate"] == (0, 1)
