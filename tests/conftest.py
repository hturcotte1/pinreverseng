from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from autopilot.brain import HeuristicBrain
from autopilot.clock import FakeClock
from autopilot.engine import Autopilot
from autopilot.integrations.calendar import InMemoryCalendar
from autopilot.integrations.mail import InMemoryMailbox
from autopilot.integrations.slack import RecordingNotifier
from autopilot.models import Candidate, Card, Company, Direction, Job, Message, OutboxStatus, Recruiter
from autopilot.settings import AnswerMode, AutomationSettings, ReplySpeed
from autopilot.store import Store

COMPANY_KB = "Hybrid, 3 days a week in office in Austin.\nBenefits:\n- 16 weeks paid parental leave\n"
JOB_KB = "Compensation: $150k-$170k base plus equity.\nInterview process: screen, technical, onsite.\n"
MONDAY_9AM_ET = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


@dataclass
class World:
    agent: Autopilot
    store: Store
    clock: FakeClock
    mail: InMemoryMailbox
    calendar: InMemoryCalendar
    notifier: RecordingNotifier
    job: Job
    threads: dict[str, str]

    def reply(self, who: str, body: str, headers: dict[str, str] | None = None, sender: str | None = None) -> Card | None:
        c = self.store.candidates[who]
        msg = Message(self.store.new_id("m"), self.threads[who], Direction.INBOUND, sender or c.email,
                      "sam@acme.com", "Re: Platform Engineer", body, self.clock.now(), headers=headers or {})
        return self.agent.handle_inbound(msg)

    def card(self, who: str) -> Card:
        card = self.store.card_for_thread(self.threads[who])
        assert card is not None
        return card

    def outbox(self, who: str, status: OutboxStatus | None = None):
        card = self.card(who)
        return [o for o in self.store.outbox.values() if o.card_id == card.id and (status is None or o.status is status)]

    def run_minutes(self, minutes: int) -> None:
        self.clock.advance(minutes=minutes)
        self.agent.tick()


def make_world(**settings_kwargs) -> World:
    clock = FakeClock(MONDAY_9AM_ET)
    store = Store()
    store.add(Company("co", "Acme", knowledge=COMPANY_KB))
    store.add(Recruiter("r1", "Sam Rivera", "sam@acme.com", "America/New_York", slack_user_id="U1"))
    job = Job("j1", "Platform Engineer", "co", "r1", knowledge=JOB_KB)
    store.add(job)
    for key in ("ana", "bo", "cy", "di"):
        store.add(Candidate(key, f"{key.title()} Tester", f"{key}@example.com", timezone="America/New_York"))
    defaults = dict(answer_mode=AnswerMode.AUTO, reply_speed=ReplySpeed.NATURAL)
    defaults.update(settings_kwargs)
    mail, calendar, notifier = InMemoryMailbox(), InMemoryCalendar(), RecordingNotifier()
    agent = Autopilot(store, HeuristicBrain(), mail, calendar, notifier, clock, AutomationSettings(**defaults),
                      random.Random(1))
    threads = {k: agent.record_outreach(store.candidates[k], job, "Platform Engineer", "Hi! Interested?")
               for k in ("ana", "bo", "cy", "di")}
    return World(agent, store, clock, mail, calendar, notifier, job, threads)


@pytest.fixture
def world() -> World:
    return make_world()
