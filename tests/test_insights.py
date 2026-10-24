from datetime import datetime, timedelta, timezone

from autopilot.insights import (
    HARD_OBJECTIONS,
    assess_at_risk,
    detect_objections,
    sentiment_trend,
    warmth_score,
)
from autopilot.models import Direction, Message

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


def _msg(body, direction=Direction.INBOUND, mins=0):
    return Message("m", "t", direction, "a@x.com", "r@x.com", "Re: Role", body, NOW + timedelta(minutes=mins))


def test_detect_objections_covers_the_taxonomy():
    assert detect_objections("Honestly the comp is a bit low for me") == ["comp_too_low"]
    assert "competing_offer" in detect_objections("I'm in the final rounds with another offer")
    assert "counteroffer" in detect_objections("My current company just countered")
    assert "location" in detect_objections("The commute is too far, I only want fully remote")
    assert "company_stage" in detect_objections("Seems too early — how much runway do you have?")
    assert detect_objections("Sounds great, what's the start date?") == []


def test_warmth_score_direction():
    assert warmth_score("This is amazing, I'd love to chat! Thanks so much!") > 0.3
    assert warmth_score("Unfortunately I'm not sure, maybe later I guess") < 0
    assert abs(warmth_score("Ok.")) <= 0.5


def test_sentiment_trend_detects_cooling_and_warming():
    thread_cooling = [
        _msg("This sounds great, I'd love to learn more!", mins=0),
        _msg("ok", mins=100),
    ]
    assert sentiment_trend(thread_cooling, thread_cooling[-1]) == "cooling"
    thread_warming = [
        _msg("not sure this is for me honestly", mins=0),
        _msg("Actually this is exciting — definitely keen to chat, thank you!", mins=100),
    ]
    assert sentiment_trend(thread_warming, thread_warming[-1]) == "warming"


def test_at_risk_rules():
    hard = next(iter(HARD_OBJECTIONS))
    risk, reason = assess_at_risk([hard], "steady", "interested")
    assert risk and reason
    risk, _ = assess_at_risk([], "cooling", "unclear")
    assert risk
    risk, _ = assess_at_risk([], "cooling", "not_interested")
    assert not risk          # already declined — not "at risk", just gone
    risk, _ = assess_at_risk(["location"], "steady", "unclear")
    assert risk              # unresolved soft concern on a not-yet-committed candidate
    risk, _ = assess_at_risk([], "steady", "interested")
    assert not risk
