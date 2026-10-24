from autopilot.governor import Tier, assess, governor_blockers, rearm
from autopilot.models import Effect, OutboxStatus, Stage
from conftest import make_world


def test_baseline_is_autonomous_and_healthy_job_auto_sends(world):
    world.reply("ana", "Interested! What's the salary range?")
    state = assess(world.store, "j1", world.agent.team_settings)
    assert state.tier is Tier.AUTONOMOUS
    world.run_minutes(10)
    assert world.outbox("ana", OutboxStatus.SENT)


def test_unsubscribe_spike_trips_breaker_to_draft_only():
    world = make_world(governor_unsub_trip=2)
    world.reply("ana", "Please unsubscribe me.")
    world.reply("bo", "Unsubscribe.")
    state = assess(world.store, "j1", world.agent.team_settings)
    assert state.tier is Tier.DRAFT_ONLY
    assert "j1" in world.store.breaker_tripped
    # New replies now draft even when clean and grounded.
    world.reply("cy", "Interested! What's the salary range?")
    assert world.outbox("cy", OutboxStatus.SCHEDULED) == []
    assert world.outbox("cy", OutboxStatus.DRAFT)


def test_breaker_is_sticky_until_rearm():
    world = make_world(governor_unsub_trip=1)
    world.reply("ana", "unsubscribe")
    assert assess(world.store, "j1", world.agent.team_settings).tier is Tier.DRAFT_ONLY
    assert rearm(world.store, "j1") is True
    assert assess(world.store, "j1", world.agent.team_settings).tier is Tier.AUTONOMOUS
    assert rearm(world.store, "j1") is False  # nothing left to clear


def test_assisted_tier_drafts_irreversible_actions():
    from autopilot.governor import GovernorState
    state = GovernorState(Tier.ASSISTED)
    assert governor_blockers(state, [Effect("offered_slots")]) == []       # reversible: fine
    assert governor_blockers(state, [Effect("book_meeting")])              # irreversible: needs review
    assert governor_blockers(GovernorState(Tier.AUTONOMOUS), [Effect("book_meeting")]) == []


def test_governor_off_is_always_autonomous():
    world = make_world(autonomy_governor=False, governor_unsub_trip=1)
    world.reply("ana", "unsubscribe")
    assert assess(world.store, "j1", world.agent.team_settings).tier is Tier.AUTONOMOUS


def test_confirmation_still_drafts_not_silenced_under_breaker():
    # Even tripped to draft-only, a time-critical confirmation is drafted (floor), never dropped.
    world = make_world(governor_unsub_trip=1)
    world.reply("ana", "Interested!")
    world.run_minutes(10)
    world.reply("ana", "The first one works.")
    world.run_minutes(10)
    card = world.card("ana")
    assert card.meeting is not None
    world.store.breaker_tripped["j1"] = "manual trip for test"
    from datetime import timedelta
    world.clock.set(card.meeting.start - timedelta(hours=2))
    world.agent.tick()
    confirms = [o for o in world.outbox("ana") if o.kind == "confirm"]
    assert confirms and confirms[0].status is OutboxStatus.DRAFT  # drafted, not silently suppressed
