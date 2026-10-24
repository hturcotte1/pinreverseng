"""Recruiter notifications: status heads-ups and open questions.

The Slack notifier DMs the recruiter from the app, keeps an "Open questions" list on
the App Home tab, and maps a reply in a question's DM thread back to that question.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from autopilot.models import OpenQuestion, Recruiter


class Notifier(Protocol):
    def status_update(self, recruiter: Recruiter, text: str) -> None: ...

    def ask(self, recruiter: Recruiter, question: OpenQuestion, context: str) -> str: ...

    def refresh(self, recruiter: Recruiter, open_questions: list[tuple[OpenQuestion, str]]) -> None: ...


@dataclass
class RecordingNotifier:
    """Keeps everything in memory; used by tests and the demo."""

    updates: list[tuple[str, str]] = field(default_factory=list)
    questions: list[tuple[str, OpenQuestion, str]] = field(default_factory=list)
    home: dict[str, list[str]] = field(default_factory=dict)

    def status_update(self, recruiter: Recruiter, text: str) -> None:
        self.updates.append((recruiter.id, text))

    def ask(self, recruiter: Recruiter, question: OpenQuestion, context: str) -> str:
        self.questions.append((recruiter.id, question, context))
        return f"local:{question.id}"

    def refresh(self, recruiter: Recruiter, open_questions: list[tuple[OpenQuestion, str]]) -> None:
        self.home[recruiter.id] = [q.question for q, _ in open_questions]


class SlackNotifier:
    API = "https://slack.com/api/"

    def __init__(self, bot_token: str, post: Any = None):
        self.bot_token = bot_token
        self._post = post or self._http_post

    def _http_post(self, method: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.API + method,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.bot_token}", "Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https endpoint
            data = json.loads(resp.read())
        if not data.get("ok"):
            raise RuntimeError(f"Slack {method} failed: {data.get('error')}")
        return data

    def status_update(self, recruiter: Recruiter, text: str) -> None:
        if recruiter.slack_user_id:
            self._post("chat.postMessage", {"channel": recruiter.slack_user_id, "text": text})

    def ask(self, recruiter: Recruiter, question: OpenQuestion, context: str) -> str:
        if not recruiter.slack_user_id:
            return ""
        text = f":question: *Open question* - {context}\n>{question.question}\nReply in this thread and I'll follow up."
        data = self._post("chat.postMessage", {"channel": recruiter.slack_user_id, "text": text})
        return f"{data['channel']}:{data['ts']}"

    def refresh(self, recruiter: Recruiter, open_questions: list[tuple[OpenQuestion, str]]) -> None:
        if not recruiter.slack_user_id:
            return
        blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": "Open questions"}}]
        if not open_questions:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "Nothing waiting on you."}})
        for q, context in open_questions:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{context}*\n>{q.question}"}})
        self._post("views.publish", {"user_id": recruiter.slack_user_id, "view": {"type": "home", "blocks": blocks}})


def verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str,
                           now: float | None = None, tolerance: int = 300) -> bool:
    """Slack request signing (v0): HMAC-SHA256 over 'v0:{timestamp}:{body}'."""
    if abs((now or time.time()) - int(timestamp)) > tolerance:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def thread_reply_from_event(payload: dict) -> tuple[str, str] | None:
    """Extract (question ref, answer text) from an Events API DM thread reply."""
    event = payload.get("event", {})
    if event.get("type") != "message" or event.get("bot_id") or event.get("subtype"):
        return None
    thread_ts = event.get("thread_ts")
    if not thread_ts or thread_ts == event.get("ts"):
        return None
    return f"{event['channel']}:{thread_ts}", event.get("text", "").strip()


@dataclass
class SlackEventsIngress:
    """Framework-agnostic Slack Events API ingress.

    Wire `handle(headers, raw_body)` to your HTTP route. It verifies the request
    signature, answers the url_verification handshake, de-duplicates retried deliveries
    by event id, and routes a recruiter's DM-thread reply to `on_answer(ref, text)`
    (normally `Autopilot.on_slack_reply`). Returns (status_code, body) for the HTTP layer.
    """

    signing_secret: str
    on_answer: "Callable[[str, str], object]"
    _seen: set[str] = field(default_factory=set)

    def handle(self, headers: dict[str, str], raw_body: bytes, now: float | None = None) -> tuple[int, str]:
        h = {k.lower(): v for k, v in headers.items()}
        ts = h.get("x-slack-request-timestamp", "")
        sig = h.get("x-slack-signature", "")
        if not verify_slack_signature(self.signing_secret, ts, raw_body, sig, now=now):
            return 401, "bad signature"
        try:
            payload = json.loads(raw_body)
        except (ValueError, TypeError):
            return 400, "bad json"
        if payload.get("type") == "url_verification":
            return 200, payload.get("challenge", "")
        event_id = payload.get("event_id")
        if event_id:
            if event_id in self._seen:  # Slack retries deliveries; process each once
                return 200, "duplicate"
            self._seen.add(event_id)
        parsed = thread_reply_from_event(payload)
        if parsed:
            ref, text = parsed
            if text:
                self.on_answer(ref, text)
        return 200, "ok"
