from autopilot.flightplan import build
from autopilot.brain.base import AnswerClaim, ComposeResult
from autopilot.models import Effect, OfferedSlot, OutboxStatus
from conftest import make_world


def test_flightplan_build_classifies_effects_and_cites_facts():
    result = ComposeResult("On compensation: $150k-$170k.", [AnswerClaim("pay?", True, ["J1"], 0.9)])
    effects = [Effect("offered_slots", slots=[OfferedSlot.__new__(OfferedSlot)]),
               Effect("book_meeting")]
    fp = build("card1", "reply", "On compensation: $150k-$170k.", result, effects,
               auto_send=True, review_reasons=[])
    assert fp.cited_fact_ids == ["J1"]
    assert "offered_slots" in fp.reversible_effects
    assert fp.has_irreversible and "book_meeting" in fp.irreversible_effects
    assert fp.auto_send


def test_engine_attaches_flightplan_to_every_outbound(world):
    world.reply("ana", "Interested! What's the salary range?")
    world.run_minutes(10)
    plans = world.agent.flight_plans(world.card("ana").id)
    assert plans, "expected at least one flight plan"
    fp = plans[-1]
    assert fp.kind == "reply" and fp.body_preview
    assert fp.auto_send is True  # clean grounded reply auto-sent


def test_flightplan_records_why_a_message_was_drafted():
    world = make_world()
    world.store.companies["co"].knowledge += "We do not offer visa sponsorship.\n"
    world.reply("ana", "Interested, but I'd need visa sponsorship.")
    fp = world.agent.flight_plans(world.card("ana").id)[-1]
    assert fp.auto_send is False
    assert fp.review_reasons  # names the deal-breaker / human reason
