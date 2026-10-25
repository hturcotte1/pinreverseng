"""Drive a rich scenario through the real engine and dump its state to JSON for the UI."""
import json
import random
from datetime import datetime, timedelta, timezone

from autopilot.brain import HeuristicBrain
from autopilot.clock import FakeClock
from autopilot.engine import Autopilot
from autopilot.governor import assess
from autopilot.coverage import readiness_report
from autopilot.ledger import funnel
from autopilot.reports import agent_stats
from autopilot.integrations.calendar import InMemoryCalendar
from autopilot.integrations.mail import InMemoryMailbox
from autopilot.integrations.slack import RecordingNotifier
from autopilot.models import Candidate, Company, Direction, Job, Message, OutboxStatus, Recruiter
from autopilot.settings import AnswerMode, AutomationSettings
from autopilot.store import Store
from autopilot.voice import calibrate

COMPANY_KB = """\
Office: hybrid, 3 days a week (Tue-Thu) in the San Francisco office.
Benefits:
- Medical, dental and vision fully covered
- 401k with 4% match
- 16 weeks paid parental leave
Founded 2019, Series B, 140 people.
"""
JOB_KB = """\
Compensation: $185k-$215k base plus equity.
Interview process: a 45-min recruiter screen, a technical deep-dive, then a half-day onsite. About 3 weeks.
Team: reports to the Director of Platform Engineering; a team of 7 backend engineers.
Tech stack: Go, Kafka, Postgres on AWS.
"""
VOICE_SAMPLES = [
    "Hey Taylor!\n\nYour work on distributed systems really caught my eye - would love to chat.\n\nCheers,\nMorgan",
    "Hey Sam!\n\nThis one felt like a great match for your background. Grab a time?\n\nCheers,\nMorgan",
    "Hey Priya!\n\nQuick one - I think you'd love what this team is building. Keen?\n\nCheers,\nMorgan",
]

clock = FakeClock(datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc))  # Mon 9am ET
store = Store()
store.add(Company("co", "Northwind Robotics", knowledge=COMPANY_KB))
store.add(Recruiter("rec", "Morgan Reyes", "morgan@northwind.com", "America/New_York", slack_user_id="U1",
                    style=calibrate(VOICE_SAMPLES, "Morgan")))
job = Job("job", "Senior Backend Engineer", "co", "rec", description="Own fleet telemetry ingestion (Go/Kafka).",
          knowledge=JOB_KB)
store.add(job)
people = [("alice", "Alice Chen", "America/Los_Angeles"), ("ben", "Ben Ortiz", None),
          ("carla", "Carla Gomez", None), ("dev", "Dev Patel", None), ("erin", "Erin Walsh", None),
          ("frank", "Frank Muller", "America/Chicago"), ("grace", "Grace Kim", None)]
for key, name, tz in people:
    store.add(Candidate(key, name, f"{key}@example.com", timezone=tz))

cal = InMemoryCalendar()
cal.block("Team standup", datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc),
          datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc))
notifier = RecordingNotifier()
agent = Autopilot(store, HeuristicBrain(), InMemoryMailbox(), cal, notifier, clock,
                  AutomationSettings(answer_mode=AnswerMode.AUTO), random.Random(7))
threads = {k: agent.record_outreach(store.candidates[k], job, "Senior Backend Engineer at Northwind",
                                    f"Hi {store.candidates[k].first_name}, your work caught my eye...")
           for k, *_ in people}


def say(who, body, headers=None):
    agent.handle_inbound(Message(store.new_id("m"), threads[who], Direction.INBOUND, store.candidates[who].email,
                                 "morgan@northwind.com", "Re: Senior Backend Engineer at Northwind", body,
                                 clock.now(), headers=headers or {}))


def settle(minutes=12):
    clock.advance(minutes=minutes)
    agent.tick()


say("alice", "Hi Morgan, I'm really interested! What's the salary range? Is the role remote or hybrid? "
    "And do you sponsor visas?")
say("ben", "Appreciate it, but I'm really happy where I am right now. Not for me, thanks.")
say("carla", "Interested! Though honestly the base looks a bit low compared to what I'm seeing elsewhere.")
say("dev", "Automatic reply: I'm out of the office until Nov 3 with limited access to email.",
    headers={"Auto-Submitted": "auto-replied"})
say("erin", "Not right now - just started something new. Maybe reach back out in March?")
say("frank", "Sure, happy to chat.")
say("grace", "Thanks, but I think I'll pass - the role seems too junior for me.")
settle()

# Morgan answers Alice's visa question in Slack.
for _, q, _ in notifier.questions:
    if "visa" in q.question.lower():
        agent.on_slack_reply(q.notifier_ref, "We're not able to sponsor visas for this role, unfortunately.")
settle()
say("alice", "Got it, thanks! The second time works great for me.")
settle()

# Walk Alice up to her interview so her thread shows prep + day-of confirmation.
_alice = store.card_for_thread(threads["alice"])
if _alice.meeting:
    clock.set(_alice.meeting.start - timedelta(hours=20)); agent.tick(); settle()
    clock.set(_alice.meeting.start - timedelta(hours=2, minutes=30)); agent.tick(); settle()

ROLE = {True: "agent"}


def role_of(m):
    if m.sent_by_agent:
        return "agent"
    return "recruiter" if m.direction is Direction.OUTBOUND else "candidate"


def card_json(c):
    cand = store.candidates[c.candidate_id]
    meeting = None
    if c.meeting:
        meeting = {"start": c.meeting.start.isoformat(),
                   "label": __import__("autopilot.scheduling", fromlist=["format_slot"]).format_slot(
                       c.meeting.start, "America/New_York")}
    return {"candidate": cand.name, "stage": c.stage.value, "interest": c.interest.value,
            "latest_activity": c.latest_activity, "open_item": c.open_item,
            "interview_status": c.interview_status.value if c.interview_status else None,
            "archive_reason": c.archive_reason.value if c.archive_reason else None,
            "sentiment": c.sentiment, "objections": c.objections, "at_risk": c.at_risk,
            "at_risk_reason": c.at_risk_reason, "meeting": meeting}


cards = [card_json(c) for c in store.cards_for_job("job")]

alice = store.card_for_thread(threads["alice"])
timeline = []
for m in store.threads[threads["alice"]]:
    if m.body.strip():
        timeline.append({"type": "message", "role": role_of(m), "at": m.sent_at.isoformat(),
                         "sent_by_agent": m.sent_by_agent, "body": m.body.strip()})
for e in store.events:
    if e.card_id == alice.id:
        timeline.append({"type": "event", "at": e.at.isoformat(), "text": e.text})
timeline.sort(key=lambda x: x["at"])

plans = [fp.as_dict() for fp in agent.flight_plans(alice.id)]
fn = funnel(store, clock.now(), "job")
gov = assess(store, "job", agent.team_settings)
rep = readiness_report(agent._kb(job), clock.now())
stats = agent_stats(store, "job").as_dict()

out = {
    "job": {"title": job.title, "company": "Northwind Robotics", "recruiter": "Morgan Reyes"},
    "now": clock.now().isoformat(),
    "generated_for": "clean-room recruiting autopilot (demo data)",
    "cards": cards,
    "alice": {"name": "Alice Chen", "timeline": timeline, "flight_plans": plans,
              "sentiment": alice.sentiment, "objections": alice.objections, "at_risk": alice.at_risk,
              "meeting": card_json(alice)["meeting"]},
    "funnel": fn.as_dict(), "leaks": [l.__dict__ for l in fn.leaks],
    "governor": {"tier": gov.tier.name, "block_rate": gov.block_rate, "decisions": gov.decisions,
                 "unsubscribes": gov.unsubscribes, "reasons": gov.reasons},
    "readiness": rep,
    "stats": stats,
    "slack": {"updates": [t for _, t in notifier.updates],
              "open_questions": [q.question for q in store.open_questions() if not q.is_open] or
                                [q.question for q in store.questions.values()]},
}
import os
_here = os.path.dirname(os.path.abspath(__file__))
_tpl = open(os.path.join(_here, "template.html")).read()
open(os.path.join(_here, "index.html"), "w").write(_tpl.replace("__DATA__", json.dumps(out, indent=2)))
print("wrote", os.path.join(_here, "index.html"))
