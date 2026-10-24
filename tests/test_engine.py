from datetime import date, datetime, timedelta, timezone

from autopilot.models import ArchiveReason, InterviewStatus, Job, OutboxStatus, Stage
from autopilot.settings import AnswerMode, AutomationSettings, ReplySpeed, ScheduleMode, SchedulingSettings
from conftest import make_world


def sent_bodies(world, who):
    return [o.body for o in world.outbox(who, OutboxStatus.SENT)]


def test_genuine_reply_opens_card_and_halts_sequences(world):
    world.reply("ana", "Happy to chat!")
    card = world.card("ana")
    assert card.stage is Stage.INTERVIEWING  # interested -> qualifying -> times offered
    assert "ana" in world.store.responded
    texts = [e.text for e in world.store.events if e.card_id == card.id]
    assert texts[:3] == ["Candidate responded to outreach sequence", "Ana Tester moved to Qualifying",
                         "Ana Tester moved to Interviewing"]


def test_automated_messages_never_open_a_card(world):
    world.reply("ana", "Thanks for your email", headers={"Auto-Submitted": "auto-replied"})
    world.reply("bo", "I am currently out of the office until Nov 3.")
    world.reply("cy", "Delivery Status Notification (Failure)", sender="mailer-daemon@example.com")
    assert world.store.cards == {}
    assert world.store.responded == set()


def test_human_mention_of_out_of_office_is_a_genuine_reply(world):
    world.reply("ana", "I'm out of office next week but interested - can we talk after the 3rd?")
    assert world.card("ana").stage is not None
    assert "ana" in world.store.responded


def test_answers_from_kb_escalates_unknowns_and_waits_natural_pace(world):
    world.reply("ana", "What's the salary range? Do you offer relocation help?")
    [item] = world.outbox("ana")
    assert item.status is OutboxStatus.SCHEDULED
    assert timedelta(minutes=3) <= item.send_at - world.clock.now() <= timedelta(minutes=9)
    assert "$150k-$170k" in item.body
    assert "confirm that with the team" in item.body
    [(_, question, _)] = world.notifier.questions
    assert "relocation" in question.question.lower()
    assert world.mail.sent == []  # nothing goes out before the delay
    world.run_minutes(10)
    assert len(world.mail.sent) == 1
    assert any(m.sent_by_agent for m in world.store.threads[world.threads["ana"]])
    assert world.card("ana").open_item.startswith("checking with recruiter")


def test_rapid_second_message_supersedes_pending_reply(world):
    world.reply("ana", "What's the salary range?")
    world.clock.advance(minutes=1)
    world.reply("ana", "Oh and what's the interview process?")
    statuses = sorted(o.status.value for o in world.outbox("ana"))
    assert statuses == ["cancelled", "scheduled"]
    pending = world.outbox("ana", OutboxStatus.SCHEDULED)[0]
    assert "screen, technical, onsite" in pending.body


def test_booking_flow_auto(world):
    world.job.settings_override = AutomationSettings(
        answer_mode=AnswerMode.AUTO, scheduling=SchedulingSettings(attendees=["hm@acme.com"]))
    world.reply("ana", "Sounds great, happy to chat")
    world.run_minutes(10)
    offered = world.card("ana").offered_slots
    assert len(offered) == 3
    world.reply("ana", "The second one works for me")
    world.run_minutes(10)
    card = world.card("ana")
    assert card.interview_status is InterviewStatus.SCHEDULED and card.meeting.start == offered[1].start
    [event] = world.calendar.events.values()
    assert set(event.attendees) == {"ana@example.com", "sam@acme.com", "hm@acme.com"}
    assert "You're all set" in sent_bodies(world, "ana")[-1]
    assert any("Booked Ana Tester" in text for _, text in world.notifier.updates)

    world.clock.set(card.meeting.end + timedelta(minutes=5))
    world.agent.tick()
    assert card.interview_status is InterviewStatus.INTERVIEWED


def test_draft_mode_defers_booking_until_recruiter_sends():
    world = make_world(answer_mode=AnswerMode.DRAFT)
    world.reply("ana", "Interested!")
    [offer] = world.outbox("ana", OutboxStatus.DRAFT)
    assert offer.draft_ref in world.mail.drafts
    assert world.card("ana").latest_activity.startswith("Draft ready")
    assert world.card("ana").offered_slots == []  # not offered until the draft actually goes out
    world.agent.approve_draft(offer.id)
    world.reply("ana", "First one please")
    [confirm] = world.outbox("ana", OutboxStatus.DRAFT)
    assert world.calendar.events == {}
    world.agent.approve_draft(confirm.id)
    assert len(world.calendar.events) == 1
    assert world.card("ana").interview_status is InterviewStatus.SCHEDULED


def test_slot_taken_before_confirmation_sends_apology_with_new_times(world):
    world.reply("ana", "Interested")
    world.run_minutes(10)
    slot = world.card("ana").offered_slots[0]
    world.reply("ana", "The first one!")
    world.calendar.block("conflict", slot.start, slot.end)  # someone grabbed it meanwhile
    world.run_minutes(10)
    world.run_minutes(10)
    assert world.card("ana").meeting is None
    last = sent_bodies(world, "ana")[-1]
    assert "Apologies" in last and "Would any of these times work" in last


def test_reschedule_cancels_event_and_offers_new_times(world):
    world.reply("ana", "Interested")
    world.run_minutes(10)
    world.reply("ana", "Option 1 works")
    world.run_minutes(10)
    assert len(world.calendar.events) == 1
    world.reply("ana", "Something came up - can we reschedule?")
    world.run_minutes(10)
    card = world.card("ana")
    assert world.calendar.events == {} and card.meeting is None
    assert card.interview_status is InterviewStatus.NOT_SCHEDULED
    assert len(card.offered_slots) == 3


def test_not_interested_archives_with_polite_close(world):
    world.reply("bo", "No thanks, I'm happy where I am.")
    card = world.card("bo")
    assert card.stage is Stage.ARCHIVED and card.archive_reason is ArchiveReason.NOT_INTERESTED
    assert card.latest_activity == "Declined - staying at current company"
    world.run_minutes(10)
    assert "keep you in mind" in sent_bodies(world, "bo")[0]


def test_not_interested_kept_when_auto_archive_off():
    world = make_world(archive_not_interested=False)
    world.reply("bo", "Not interested, thanks")
    assert world.card("bo").stage is Stage.REPLIED


def test_unsubscribe_is_final(world):
    world.reply("cy", "Please remove me from your list.")
    card = world.card("cy")
    assert card.archive_reason is ArchiveReason.UNSUBSCRIBED
    assert "cy@example.com" in world.store.suppressed
    world.reply("cy", "Actually what's the salary?")
    world.run_minutes(10)
    assert world.mail.sent == [] and world.outbox("cy") == []


def test_nudge_then_archive_unresponsive():
    world = make_world(nudge_after_days=3, archive_unresponsive_after_days=7)
    world.reply("ana", "Sure, happy to chat")
    world.run_minutes(10)
    for _ in range(3):
        world.clock.advance(days=1)
        world.agent.tick()
    world.clock.advance(hours=24)  # nudge goes out in the candidate's morning window
    world.agent.tick()
    nudges = [o for o in world.outbox("ana") if o.kind == "nudge"]
    assert len(nudges) == 1 and nudges[0].status is OutboxStatus.SENT
    assert "Would" in nudges[0].body or "would" in nudges[0].body  # fresh times offered
    for _ in range(7):
        world.clock.advance(days=1)
        world.agent.tick()
    card = world.card("ana")
    assert card.stage is Stage.ARCHIVED and card.archive_reason is ArchiveReason.UNRESPONSIVE


def test_archived_unresponsive_candidate_can_come_back():
    world = make_world(nudge_after_days=None, archive_unresponsive_after_days=2)
    world.reply("ana", "Happy to chat")
    world.run_minutes(10)
    world.clock.advance(days=3)
    world.agent.tick()
    assert world.card("ana").stage is Stage.ARCHIVED
    world.reply("ana", "Sorry for the delay - still interested!")
    assert world.card("ana").stage in (Stage.QUALIFYING, Stage.INTERVIEWING)


def test_slack_answer_is_learned_and_reused(world):
    world.reply("ana", "Do you sponsor visas?")
    [(_, q, _)] = world.notifier.questions
    world.run_minutes(10)
    item = world.agent.on_slack_reply(q.notifier_ref, "Sadly we can't sponsor visas for this role.")
    assert item is not None and "can't sponsor" in item.body
    assert world.notifier.home["r1"] == []  # App Home open-questions list cleared
    world.reply("bo", "Interested. Do you sponsor visas?")
    assert len(world.notifier.questions) == 1  # no second escalation
    assert "can't sponsor visas" in world.outbox("bo")[0].body


def test_learning_disabled_keeps_kb_unchanged():
    world = make_world(allow_learning=False)
    world.reply("ana", "Do you sponsor visas?")
    [(_, q, _)] = world.notifier.questions
    world.agent.on_recruiter_answer(q.id, "No sponsorship.")
    assert world.store.learned.for_job(world.job) == []


def test_internal_only_answer_is_not_learned_and_reply_is_drafted(world):
    world.reply("ana", "Do you sponsor visas?")
    [(_, q, _)] = world.notifier.questions
    item = world.agent.on_recruiter_answer(q.id, "Internal only: legal is still deciding.")
    assert world.store.learned.for_job(world.job) == []
    assert item.status is OutboxStatus.DRAFT


def test_human_takeover_switches_agent_to_drafts(world):
    world.reply("ana", "What's the salary range?")
    world.agent.record_human_reply(world.threads["ana"], "Hey Ana, I'll take this one.")
    assert world.outbox("ana")[0].status is OutboxStatus.CANCELLED
    world.reply("ana", "And the interview process?")
    latest = world.outbox("ana")[-1]
    assert latest.status is OutboxStatus.DRAFT
    assert "recruiter wrote in this thread recently" in latest.review_reasons


def test_manual_move_is_new_baseline(world):
    world.reply("ana", "What's the salary range?")
    card = world.card("ana")
    world.agent.move_card(card.id, Stage.INTERVIEWING)
    world.reply("ana", "What's the interview process?")
    assert card.stage is Stage.INTERVIEWING


def test_offer_accepted_moves_to_hired_and_agent_steps_back(world):
    world.reply("ana", "Interested")
    world.reply("ana", "I'm thrilled to accept the offer!")
    card = world.card("ana")
    assert card.stage is Stage.HIRED
    world.reply("ana", "What's the start date?")
    assert world.outbox("ana", OutboxStatus.SCHEDULED) == []


def test_sensitive_messages_go_to_a_human(world):
    world.reply("ana", "I have a competing offer at a higher number - can we negotiate?")
    [item] = world.outbox("ana")
    assert item.status is OutboxStatus.DRAFT
    assert any("needs your attention" in text for _, text in world.notifier.updates)


def test_dealbreaker_blocks_scheduling_and_alerts():
    world = make_world()
    world.store.companies["co"].knowledge += "\nWe do not offer visa sponsorship.\n"
    world.reply("ana", "Interested, but I would need visa sponsorship.")
    card = world.card("ana")
    assert card.offered_slots == [] and card.stage is Stage.QUALIFYING
    assert any("deal-breaker" in text for _, text in world.notifier.updates)


def test_scheduling_link_mode_sends_link_once():
    world = make_world(scheduling=SchedulingSettings(mode=ScheduleMode.LINK, scheduling_link="https://cal.com/sam"))
    world.reply("ana", "Interested!")
    world.run_minutes(10)
    assert "https://cal.com/sam" in sent_bodies(world, "ana")[0]
    world.reply("ana", "What's the salary range?")
    world.run_minutes(10)
    assert "https://cal.com/sam" not in sent_bodies(world, "ana")[1]


def test_do_not_schedule_mode_just_notifies():
    world = make_world(scheduling=SchedulingSettings(mode=ScheduleMode.NONE))
    world.reply("ana", "Interested! Tuesday afternoon works.")
    assert world.card("ana").offered_slots == []
    assert any("ready to schedule" in text for _, text in world.notifier.updates)


def test_candidate_proposed_time_is_booked_directly(world):
    # Thursday Oct 29, 2pm ET is free and inside the window.
    world.reply("ana", "Interested! Could we do Thursday at 2pm?")
    world.run_minutes(10)
    card = world.card("ana")
    assert card.meeting is not None
    assert card.meeting.start == datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc)


def test_per_job_override_replaces_team_defaults(world):
    world.store.jobs["j2"] = job2 = Job("j2", "Data Engineer", "co", "r1",
                                        settings_override=AutomationSettings(answer_mode=AnswerMode.DRAFT))
    thread = world.agent.record_outreach(world.store.candidates["di"], job2, "Data Engineer", "Hi")
    world.threads["di"] = thread
    world.reply("di", "What's the interview process?")
    assert world.outbox("di")[0].status is OutboxStatus.DRAFT


def test_confidential_client_is_never_named(world):
    world.store.companies["co"].hide_details = True
    world.store.jobs["j1"].knowledge += "Acme was founded in 2015.\n"
    world.reply("ana", "When was the company founded?")
    body = world.outbox("ana")[0].body
    assert "Acme" not in body


def test_instant_reply_speed():
    world = make_world(reply_speed=ReplySpeed.INSTANT)
    world.reply("ana", "What's the salary range?")
    item = world.outbox("ana")[0]
    assert item.send_at - world.clock.now() <= timedelta(seconds=20)


def test_daily_agent_reply_cap():
    world = make_world(max_agent_replies_per_day=1)
    world.reply("ana", "What's the salary range?")
    world.run_minutes(10)
    world.reply("ana", "And the interview process?")
    assert world.outbox("ana")[-1].status is OutboxStatus.DRAFT


def test_not_now_schedules_reviewed_reengagement(world):
    world.reply("cy", "Not right now, just started a new role. Maybe reach out in March?")
    card = world.card("cy")
    assert card.follow_up_on.date() == date(2027, 3, 1)
    world.run_minutes(10)
    assert "reach back out around March 1" in sent_bodies(world, "cy")[0]
    world.clock.set(datetime(2027, 3, 1, 16, 0, tzinfo=timezone.utc))
    world.agent.tick()
    reengage = [o for o in world.outbox("cy") if o.kind == "reengage"]
    assert reengage and reengage[0].status is OutboxStatus.DRAFT
    assert card.stage is not Stage.ARCHIVED  # not auto-archived while parked
