import hashlib
import hmac

from autopilot.integrations.slack import SlackNotifier, thread_reply_from_event, verify_slack_signature
from autopilot.models import OpenQuestion, Recruiter


def test_signature_verification():
    secret, ts, body = "s3cret", "1700000000", b'{"type":"event_callback"}'
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    assert verify_slack_signature(secret, ts, body, sig, now=1700000100)
    assert not verify_slack_signature(secret, ts, body, sig, now=1700001000)  # replayed too late
    assert not verify_slack_signature("wrong", ts, body, sig, now=1700000100)


def test_thread_reply_extraction_ignores_bots_and_top_level():
    reply = {"event": {"type": "message", "channel": "D1", "ts": "2.0", "thread_ts": "1.0", "text": " No visas "}}
    assert thread_reply_from_event(reply) == ("D1:1.0", "No visas")
    assert thread_reply_from_event({"event": {**reply["event"], "bot_id": "B1"}}) is None
    assert thread_reply_from_event({"event": {"type": "message", "channel": "D1", "ts": "1.0"}}) is None


def test_ask_posts_dm_and_returns_thread_ref():
    calls = []

    def post(method, payload):
        calls.append((method, payload))
        return {"ok": True, "channel": "D1", "ts": "123.456"}

    notifier = SlackNotifier("xoxb-test", post=post)
    from datetime import datetime, timezone
    q = OpenQuestion("q1", "card", "Do you sponsor visas?", datetime(2026, 1, 1, tzinfo=timezone.utc))
    ref = notifier.ask(Recruiter("r", "Sam", "s@x.com", slack_user_id="U1"), q, "Ana · Eng")
    assert ref == "D1:123.456"
    assert calls[0][0] == "chat.postMessage" and calls[0][1]["channel"] == "U1"
    notifier.refresh(Recruiter("r", "Sam", "s@x.com", slack_user_id="U1"), [(q, "Ana · Eng")])
    assert calls[1][0] == "views.publish" and calls[1][1]["view"]["type"] == "home"
