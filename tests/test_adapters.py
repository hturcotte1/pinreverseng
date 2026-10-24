"""Tests for the pure parts of the reference production adapters (no network, no SDK)."""

import base64
import json

from autopilot.integrations.gmail import build_mime, gmail_raw, parse_gmail_message
from autopilot.integrations.google_calendar import event_body, parse_freebusy
from autopilot.integrations.graph import GraphMailbox, build_send_payload, parse_graph_message
from autopilot.integrations.slack import SlackEventsIngress, verify_slack_signature
from autopilot.models import Direction
from datetime import datetime, timezone


# -- Gmail --------------------------------------------------------------------------------
def test_build_mime_sets_threading_headers():
    msg = build_mime("sam@acme.com", "ana@x.com", "Re: Role", "Hi Ana", in_reply_to="<abc@mail>",
                     references=["<root@mail>"])
    assert msg["In-Reply-To"] == "<abc@mail>"
    assert msg["References"] == "<root@mail> <abc@mail>"
    assert "Hi Ana" in msg.get_content()


def test_gmail_raw_roundtrips():
    msg = build_mime("s@x.com", "a@x.com", "Hello", "Body text")
    decoded = base64.urlsafe_b64decode(gmail_raw(msg)).decode()
    assert "Subject: Hello" in decoded and "Body text" in decoded


def test_parse_gmail_message_prefers_plaintext_and_extracts_address():
    def b64(text):
        return base64.urlsafe_b64encode(text.encode()).decode()

    resource = {
        "id": "g1",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Ana Tester <ana@example.com>"},
                {"name": "To", "value": "sam@acme.com"},
                {"name": "Subject", "value": "Re: Role"},
                {"name": "Date", "value": "Mon, 26 Oct 2026 09:00:00 -0400"},
                {"name": "Message-ID", "value": "<m1@mail>"},
                {"name": "Auto-Submitted", "value": "no"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("Interested, thanks!")}},
                {"mimeType": "text/html", "body": {"data": b64("<p>Interested, thanks!</p>")}},
            ],
        },
    }
    msg = parse_gmail_message(resource, "thr_1")
    assert msg.sender == "ana@example.com"
    assert msg.direction is Direction.INBOUND
    assert msg.body == "Interested, thanks!"
    assert msg.headers["Message-ID"] == "<m1@mail>"
    assert msg.sent_at.tzinfo is not None


# -- Microsoft Graph ----------------------------------------------------------------------
def test_parse_graph_message_strips_html():
    resource = {
        "id": "AAMk", "subject": "Re: Role", "receivedDateTime": "2026-10-26T13:00:00Z",
        "body": {"contentType": "html", "content": "<div>Sounds <b>great</b>!</div>"},
        "from": {"emailAddress": {"address": "ana@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "sam@acme.com"}}],
        "internetMessageId": "<m2@mail>",
    }
    msg = parse_graph_message(resource, "thr_1")
    assert "great" in msg.body and "<b>" not in msg.body
    assert msg.sender == "ana@example.com"
    assert msg.sent_at == datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


def test_graph_send_payload_shape():
    payload = build_send_payload("ana@x.com", "Re: Role", "Hi")
    assert payload["message"]["toRecipients"][0]["emailAddress"]["address"] == "ana@x.com"
    assert payload["message"]["body"]["contentType"] == "Text"


def test_graph_mailbox_replies_in_thread_when_resolvable():
    calls = []

    def caller(method, path, body):
        calls.append((method, path, body))
        return {"id": "draft1"} if "createReply" in path else {"id": "x"}

    box = GraphMailbox(caller, resolve=lambda t: "MSG123" if t == "thr_1" else None)
    box.send("thr_1", "ana@x.com", "Re: Role", "Hello")
    assert calls[0][1] == "/me/messages/MSG123/reply"
    box.send("thr_unknown", "ana@x.com", "Role", "Hello")
    assert calls[1][1] == "/me/sendMail"


# -- Google Calendar ----------------------------------------------------------------------
def test_parse_freebusy():
    resp = {"calendars": {"primary": {"busy": [
        {"start": "2026-10-27T14:00:00Z", "end": "2026-10-27T15:00:00Z"}]}}}
    busy = parse_freebusy(resp)
    assert busy == [(datetime(2026, 10, 27, 14, tzinfo=timezone.utc),
                     datetime(2026, 10, 27, 15, tzinfo=timezone.utc))]


def test_event_body_lists_attendees():
    body = event_body("Intro", datetime(2026, 10, 27, 14, tzinfo=timezone.utc),
                      datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc), ["a@x.com", "b@x.com"])
    assert [a["email"] for a in body["attendees"]] == ["a@x.com", "b@x.com"]
    assert body["start"]["dateTime"].startswith("2026-10-27T14:00")


# -- Slack Events ingress -----------------------------------------------------------------
def _signed(secret, body: bytes, ts="1700000000"):
    import hashlib
    import hmac
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def test_ingress_handshake_and_routing_and_dedup():
    answered = []
    ingress = SlackEventsIngress("secret", on_answer=lambda ref, text: answered.append((ref, text)))
    now = 1700000000

    challenge = json.dumps({"type": "url_verification", "challenge": "xyz"}).encode()
    assert ingress.handle(_signed("secret", challenge), challenge, now=now) == (200, "xyz")

    event = json.dumps({
        "event_id": "Ev1", "type": "event_callback",
        "event": {"type": "message", "channel": "D1", "ts": "2.0", "thread_ts": "1.0", "text": "No visas"},
    }).encode()
    assert ingress.handle(_signed("secret", event), event, now=now) == (200, "ok")
    assert answered == [("D1:1.0", "No visas")]
    # Slack retries: same event id must not answer twice.
    assert ingress.handle(_signed("secret", event), event, now=now) == (200, "duplicate")
    assert len(answered) == 1


def test_ingress_rejects_bad_signature():
    ingress = SlackEventsIngress("secret", on_answer=lambda *a: None)
    body = json.dumps({"type": "event_callback"}).encode()
    assert ingress.handle(_signed("wrong", body), body, now=1700000000)[0] == 401
