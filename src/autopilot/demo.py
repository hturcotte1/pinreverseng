"""End-to-end simulation: one job, six candidates, two simulated weeks.

    python -m autopilot.demo             # offline heuristic brain, auto-send
    python -m autopilot.demo --drafts    # everything lands as drafts for review
    python -m autopilot.demo --claude    # use Claude (needs Anthropic credentials)
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timezone

from autopilot.board import render_board, render_timeline
from autopilot.brain import HeuristicBrain
from autopilot.clock import FakeClock
from autopilot.engine import Autopilot
from autopilot.integrations.calendar import InMemoryCalendar
from autopilot.integrations.mail import InMemoryMailbox
from autopilot.integrations.slack import RecordingNotifier
from autopilot.models import Candidate, Company, Direction, Job, Message, OutboxStatus, Recruiter
from autopilot.settings import AnswerMode, AutomationSettings
from autopilot.store import Store

COMPANY_KB = """\
Office: 500 Howard St, San Francisco. Hybrid, 3 days a week in office (Tue-Thu).
Benefits:
- Medical, dental and vision fully covered for employees
- 401k with 4% match
Founded in 2019, Series B, 140 employees.
"""

JOB_KB = """\
Compensation: $185k-$215k base plus equity.
Interview process: recruiter screen, technical deep-dive, then a half-day onsite with the team. About 3 weeks end to end.
Team: reports to the Director of Platform Engineering; team of 7 backend engineers.
"""


def build(drafts: bool, use_claude: bool) -> tuple[Autopilot, Store, FakeClock, RecordingNotifier, dict[str, str]]:
    clock = FakeClock(datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc))  # Mon 9am ET
    store = Store()
    store.add(Company("co_acme", "Acme Robotics", knowledge=COMPANY_KB))
    store.add(Recruiter("rec_1", "Jordan Lee", "jordan@acmerobotics.com", "America/New_York", slack_user_id="U123"))
    store.add(Job("job_be", "Senior Backend Engineer", "co_acme", "rec_1",
                  description="Own the fleet telemetry ingestion platform (Go, Kafka, Postgres).",
                  knowledge=JOB_KB))
    people = [
        ("alice", "Alice Chen", "America/Los_Angeles"),
        ("ben", "Ben Ortiz", None),
        ("carla", "Carla Gomez", None),
        ("dev", "Dev Patel", None),
        ("erin", "Erin Walsh", None),
        ("frank", "Frank Muller", "America/Chicago"),
    ]
    for key, name, tz in people:
        store.add(Candidate(key, name, f"{key}@example.com", timezone=tz))

    settings = AutomationSettings(answer_mode=AnswerMode.DRAFT if drafts else AnswerMode.AUTO)
    if use_claude:
        from autopilot.brain.claude import ClaudeBrain

        brain = ClaudeBrain()
    else:
        brain = HeuristicBrain()
    notifier = RecordingNotifier()
    calendar = InMemoryCalendar()
    calendar.block("Team standup", datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc),
                   datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc))
    agent = Autopilot(store, brain, InMemoryMailbox(), calendar, notifier, clock, settings, random.Random(7))
    threads = {}
    for key, *_ in people:
        threads[key] = agent.record_outreach(
            store.candidates[key], store.jobs["job_be"], "Senior Backend Engineer at Acme Robotics",
            f"Hi {store.candidates[key].first_name}, your work on streaming systems caught my eye...")
    return agent, store, clock, notifier, threads


def say(agent: Autopilot, clock: FakeClock, threads: dict[str, str], who: str, body: str,
        headers: dict[str, str] | None = None) -> None:
    store = agent.store
    msg = Message(store.new_id("m"), threads[who], Direction.INBOUND, store.candidates[who].email,
                  "jordan@acmerobotics.com", "Re: Senior Backend Engineer at Acme Robotics", body, clock.now(),
                  headers=headers or {})
    print(f"\n[{clock.now():%a %b %d %H:%M} UTC] {store.candidates[who].name}: {body.strip()[:100]}")
    agent.handle_inbound(msg)


def run(drafts: bool = False, use_claude: bool = False) -> Store:
    agent, store, clock, notifier, threads = build(drafts, use_claude)

    from autopilot.coverage import readiness_report
    kb = agent._kb(store.jobs["job_be"])
    rep = readiness_report(kb, clock.now())
    print(f"--- Pre-outreach KB readiness: {rep['coverage_score']:.0%} answerable, "
          f"ready={rep['ready_for_outreach']} ---")
    for gap in rep["gaps"][:4]:
        print(f"  gap: {gap}")
    for conflict in rep["conflicts"]:
        print(f"  conflict: {conflict}")

    def approve_all() -> None:
        for item in list(store.outbox.values()):
            if item.status is OutboxStatus.DRAFT:
                agent.approve_draft(item.id)

    def settle(minutes: int = 15) -> None:
        clock.advance(minutes=minutes)
        agent.tick()
        if drafts:
            approve_all()  # simulate the recruiter reviewing and sending drafts

    say(agent, clock, threads, "alice", "Hi Jordan, thanks for reaching out! This sounds interesting. "
        "What's the salary range? Is the role remote? Also, do you sponsor visas?")
    say(agent, clock, threads, "ben", "Thanks, but I'm happy where I am. Not interested.")
    say(agent, clock, threads, "carla", "Sure, happy to chat.")
    say(agent, clock, threads, "dev", "I am currently out of the office with limited access to email.",
        headers={"Auto-Submitted": "auto-replied"})
    say(agent, clock, threads, "erin", "Not right now - I just started a new job. Maybe reach out in March?")
    settle()

    print("\n--- Slack: Jordan answers Alice's open question in the DM thread ---")
    for _, q, ctx in notifier.questions:
        print(f"Q ({ctx}): {q.question}")
        if "visa" in q.question.lower():
            print("A: We can't sponsor visas for this role right now.")
            agent.on_slack_reply(q.notifier_ref, "We can't sponsor visas for this role right now.")
    settle()

    say(agent, clock, threads, "alice", "Thanks! The second one works for me.")
    settle()
    say(agent, clock, threads, "frank", "Interested. Do you sponsor visas? And what does the interview process look like?")
    settle()

    for _ in range(14):  # two quiet weeks: Carla gets nudged, then archived
        clock.advance(days=1)
        agent.tick()
        if drafts:
            approve_all()

    print("\n--- Slack DMs ---")
    for _, text in notifier.updates:
        print(" ", text)
    print("\n--- Emails the agent sent (or the recruiter approved) ---")
    for item in store.outbox.values():
        if item.status is OutboxStatus.SENT:
            name = store.candidates[store.cards[item.card_id].candidate_id].name
            print(f"\nTo {name} [{item.kind}]:\n{item.body}")
    print("\n" + render_board(store, "job_be", clock.now()))
    from autopilot.reports import agent_stats
    s = agent_stats(store, "job_be")
    print(f"\n--- AI Agent stats ---\n  sent by agent: {s.sent_by_agent} | meetings booked: {s.meetings_booked}"
          f" | auto-handled: {s.auto_handled} | open questions: {s.open_questions}"
          f" | archived: {s.archived_by_reason}")
    from autopilot.ledger import funnel, summary_line
    fn = funnel(store, clock.now(), "job_be")
    print(f"\n--- Conversion ledger ---\n  {summary_line(fn)}")
    for leak in fn.leaks:
        print(f"  leak: {leak.candidate} lost at {leak.stage_before} ({leak.reason})")
    alice = store.card_for_thread(threads["alice"])
    print("\n--- Inbox timeline: Alice ---\n" + render_timeline(store, alice))
    print("\n--- Knowledge learned ---")
    for fact in store.learned.for_job(store.jobs["job_be"]):
        print(f"[{fact.id}] {fact.text}")
    return store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drafts", action="store_true", help="create drafts instead of auto-sending")
    parser.add_argument("--claude", action="store_true", help="use the Claude brain")
    args = parser.parse_args()
    run(drafts=args.drafts, use_claude=args.claude)


if __name__ == "__main__":
    main()
