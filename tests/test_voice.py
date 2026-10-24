from autopilot.brain import HeuristicBrain
from autopilot.brain.base import ComposePlan, ConversationContext
from autopilot.knowledge import KnowledgeBase
from autopilot.models import Candidate, Company, Direction, Job, Message, Recruiter
from autopilot.voice import DEFAULT_STYLE, calibrate, style_guide
from datetime import datetime, timezone

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)

CASUAL_SAMPLES = [
    "Hey Jordan!\n\nSaw your work on Kafka — would love to chat, it's a great team. Let me know!\n\nCheers,\nSam",
    "Hey Priya!\n\nThis one's a fun one. You'd dig the stack. Grab a time?\n\nCheers,\nSam",
    "Hey Marcus!\n\nQuick one — I think you'd love what they're building. Keen?\n\nCheers,\nSam",
]
FORMAL_SAMPLES = [
    "Dear Mr. Chen,\n\nI am reaching out regarding a senior engineering position. I would welcome the "
    "opportunity to discuss it with you.\n\nBest regards,\nSamuel",
    "Dear Ms. Patel,\n\nI am contacting you about a role that aligns with your background. Please let me "
    "know if you would be open to a conversation.\n\nBest regards,\nSamuel",
]


def test_calibrate_casual_voice():
    p = calibrate(CASUAL_SAMPLES, "Sam")
    assert p.greeting.startswith("Hey") and p.exclaim is True
    assert p.signoff.startswith("Cheers")
    assert p.formality == "casual"
    assert p.samples == 3


def test_calibrate_formal_voice():
    p = calibrate(FORMAL_SAMPLES, "Samuel")
    assert p.greeting.startswith("Dear") and p.exclaim is False
    assert "regards" in p.signoff.lower()
    assert p.formality == "formal"


def test_empty_calibration_is_safe_default():
    p = calibrate([], "Sam")
    assert p.samples == 0 and p.greeting == DEFAULT_STYLE.greeting


def _ctx(recruiter):
    msg = Message("m", "t", Direction.INBOUND, "a@x.com", "r@x.com", "Re: Role", "Sounds good", NOW)
    return ConversationContext(recruiter, Candidate("a", "Ana Tester", "a@x.com"), "Eng", "Acme", "",
                               KnowledgeBase(Company("c", "Acme"), Job("j", "Eng", "c", "r")), [msg], msg, NOW,
                               "America/New_York")


def test_heuristic_compose_applies_voice():
    recruiter = Recruiter("r", "Sam Rivera", "s@x.com", style=calibrate(CASUAL_SAMPLES, "Sam"))
    body = HeuristicBrain().compose(_ctx(recruiter), ComposePlan(kind="reply")).body
    assert body.startswith("Hey Ana!")
    assert body.rstrip().endswith("Cheers,\nSam")


def test_heuristic_compose_default_voice_unchanged():
    recruiter = Recruiter("r", "Sam Rivera", "s@x.com")  # no style
    body = HeuristicBrain().compose(_ctx(recruiter), ComposePlan(kind="reply")).body
    assert body.startswith("Hi Ana,")
    assert body.rstrip().endswith("Best,\nSam")


def test_style_guide_renders_instructions():
    guide = style_guide(calibrate(CASUAL_SAMPLES, "Sam"))
    assert "voice" in guide.lower() and "Register: casual" in guide
