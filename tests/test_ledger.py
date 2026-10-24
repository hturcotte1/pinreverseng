from datetime import timedelta

from autopilot.ledger import funnel, summary_line
from autopilot.models import OutboxStatus, Stage
from conftest import make_world


def _drive():
    """ana -> booked+interviewed; bo -> not interested; cy -> unresponsive archive."""
    world = make_world()
    world.reply("ana", "Interested, happy to chat!")
    world.run_minutes(10)
    world.reply("ana", "The first one works.")
    world.run_minutes(10)
    card = world.card("ana")
    world.clock.set(card.meeting.end + timedelta(minutes=5))
    world.agent.tick()                           # ana interviewed

    world.reply("bo", "No thanks, not interested.")   # archived: not interested

    world.reply("cy", "Interested!")                  # cy then goes silent
    world.run_minutes(10)
    for _ in range(18):                          # past the nudge (day 3) + 10-day archive clock it resets
        world.clock.advance(days=1)
        world.agent.tick()                       # cy auto-archived unresponsive
    return world


def test_funnel_counts_and_conversion():
    world = _drive()
    f = funnel(world.store, world.clock.now(), job_id="j1")
    assert f.n == 3
    assert f.counts["replied"] == 3
    assert f.counts["qualifying"] == 2           # ana + cy engaged; bo declined at Replied
    assert f.counts["interviewing"] == 2         # ana + cy reached interviewing (cy got slots offered)
    assert f.counts["booked"] == 1               # only ana booked
    assert f.counts["interviewed"] == 1
    assert f.counts["hired"] == 0
    assert 0.0 <= f.conversion["booked"] <= 1.0
    assert f.step_conversion["interviewing->booked"] == 0.5
    assert "replied 3" in summary_line(f)


def test_leak_attribution():
    world = _drive()
    f = funnel(world.store, world.clock.now(), job_id="j1")
    by_reason = {leak.candidate: (leak.stage_before, leak.reason) for leak in f.leaks}
    assert by_reason["Bo Tester"][1] == "Not Interested"
    assert by_reason["Cy Tester"][1] == "Unresponsive"
    # cy leaked from Interviewing (times were offered before silence)
    assert by_reason["Cy Tester"][0] in (Stage.INTERVIEWING.value, Stage.QUALIFYING.value)


def test_dwell_times_present():
    world = _drive()
    f = funnel(world.store, world.clock.now(), job_id="j1")
    assert any("Replied->Qualifying" in k for k in f.dwell_hours)


def test_empty_funnel_is_safe():
    world = make_world()
    f = funnel(world.store, world.clock.now(), job_id="j1")
    assert f.n == 0 and f.counts["replied"] == 0 and f.conversion["hired"] == 0.0
