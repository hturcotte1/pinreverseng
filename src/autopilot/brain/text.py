"""Text helpers shared by both brains: quote stripping and auto-reply prefiltering."""

from __future__ import annotations

import re

from autopilot.models import Message

_QUOTE_HEADER = re.compile(
    r"^(On .{5,200}wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+|Sent from my \w+)\s*$",
    re.IGNORECASE,
)


def strip_quoted(body: str) -> str:
    """Drop quoted history so only the candidate's new words are analysed."""
    kept: list[str] = []
    for line in body.splitlines():
        if _QUOTE_HEADER.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


_AUTO_SENDERS = re.compile(r"^(mailer-daemon|postmaster|no-?reply|noreply|bounce)", re.IGNORECASE)
# Phrases that only machines write - safe to match anywhere.
_AUTO_STRONG = re.compile(
    r"(automatic reply|auto-?reply|autoreply|this is an automated|delivery status notification|undeliverable|"
    r"address not found|message (was )?not delivered|mail delivery (subsystem|failed))",
    re.IGNORECASE,
)
# Phrases people also write ("I'm out of office next week, can we talk after?") - only trusted
# in the subject or the opening line of a short message that asks nothing.
_AUTO_WEAK = re.compile(
    r"(out of (the )?office|on (parental|maternity|paternity) leave|limited access to (my )?e-?mail|"
    r"i am currently away|i'm currently away)",
    re.IGNORECASE,
)


def looks_automated(msg: Message) -> bool:
    """Cheap deterministic filter run before any model call.

    Only genuine human replies should open a pipeline card or count as responses.
    """
    headers = {k.lower(): v.lower() for k, v in msg.headers.items()}
    if headers.get("auto-submitted", "no") != "no":
        return True
    if "x-autoreply" in headers or "x-autorespond" in headers:
        return True
    if headers.get("precedence") in {"bulk", "auto_reply", "junk", "list"}:
        return True
    if _AUTO_SENDERS.match(msg.sender.split("@")[0]):
        return True
    body = strip_quoted(msg.body)
    if _AUTO_STRONG.search(msg.subject) or _AUTO_STRONG.search(body[:600]) or _AUTO_WEAK.search(msg.subject):
        return True
    first_line = body.splitlines()[0] if body else ""
    return bool(_AUTO_WEAK.search(first_line)) and "?" not in body and len(body) < 600


TZ_ALIASES: dict[str, str] = {
    "pt": "America/Los_Angeles", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "mt": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver", "mountain": "America/Denver",
    "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago", "central": "America/Chicago",
    "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "eastern": "America/New_York",
    "gmt": "Europe/London", "bst": "Europe/London", "london": "Europe/London", "uk": "Europe/London",
    "cet": "Europe/Berlin", "cest": "Europe/Berlin", "berlin": "Europe/Berlin",
}


def detect_timezone(text: str) -> str | None:
    for word in re.findall(r"[A-Za-z]+", text):
        tz = TZ_ALIASES.get(word.lower())
        if tz and (word.isupper() or word.lower() in {"pacific", "mountain", "central", "eastern", "london", "berlin"}):
            return tz
    return None
