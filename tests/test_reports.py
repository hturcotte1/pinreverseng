from datetime import timedelta

from autopilot.reports import agent_stats, conversations
from conftest import make_world


def _busy_world():
    """Drive a handful of candidates to distinct end states."""
    world = make_world()
    world.reply("ana", "Interested! What's the salary range?")   # interested, gets times
    world.run_minutes(10)
    world.reply("bo", "No thanks, not interested.")              # archived: not interested
    world.reply("cy", "Do you sponsor visas?")                   # open question -> needs attention
    world.run_minutes(10)
    return world


def test_agent_stats_counts():
    world = _busy_world()
    stats = agent_stats(world.store, world.job.id)
    assert stats.total_cards == 3
    assert stats.sent_by_agent >= 2          # ana + cy got agent replies
    assert stats.open_questions == 1         # cy's visa question
    assert stats.archived_by_reason.get("Not Interested") == 1
    assert stats.by_stage.get("Interviewing") == 1  # ana reached Interviewing (times offered)


def test_agent_stats_tracks_auto_vs_human():
    world = make_world()
    world.reply("ana", "What's the salary range?")
    world.run_minutes(10)                      # auto-handled
    world.reply("bo", "What's the salary range?")
    world.agent.record_human_reply(world.threads["bo"], "I'll take this one, Bo.")
    stats = agent_stats(world.store, world.job.id)
    assert stats.auto_handled == 1
    assert stats.human_touched == 1


def test_conversations_filters():
    world = _busy_world()
    now = world.clock.now()
    interested = {c.candidate_id for c in conversations(world.store, "interested", now)}
    assert "ana" in interested and "bo" not in interested
    not_int = {c.candidate_id for c in conversations(world.store, "not_interested", now)}
    assert not_int == {"bo"}
    attention = {c.candidate_id for c in conversations(world.store, "needs_attention", now)}
    assert "cy" in attention           # has an open question
    archived = {c.candidate_id for c in conversations(world.store, "archived", now)}
    assert archived == {"bo"}


def test_conversations_rejects_bad_filter():
    world = make_world()
    import pytest
    with pytest.raises(ValueError):
        conversations(world.store, "nonsense", world.clock.now())
