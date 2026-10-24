"""Recruiter voice calibration.

Pin calibrates outreach on five sample messages from the recruiter's sent folder. We do the
same, deterministically: extract a StyleProfile (greeting, sign-off, warmth, formality, emoji,
sentence length) from past sent messages, then apply it so replies read like *that* recruiter
rather than generic AI. The heuristic composer uses the profile directly; the Claude composer
receives it as an explicit style guide in the prompt.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤️]")
_CONTRACTION = re.compile(r"\b\w+'(s|re|ll|ve|t|d|m)\b", re.IGNORECASE)
_GREETING_RX = re.compile(
    r"^\s*(hi there|hi|hey there|hey|hello|dear|good (morning|afternoon)|yo)\b[\s,!-]*([A-Z][\w.-]*)?",
    re.IGNORECASE)
_SIGNOFF_RX = re.compile(
    r"^(best regards|all the best|thanks so much|thank you|talk soon|speak soon|take care|best|regards|"
    r"cheers|thanks|warmly|sincerely)\b", re.IGNORECASE)

_FORMAL_GREETINGS = {"dear", "good morning", "good afternoon"}
_CASUAL_GREETINGS = {"hey", "hey there", "yo"}


@dataclass
class StyleProfile:
    greeting: str = "Hi {first},"   # template; {first} = candidate first name
    signoff: str = "Best,\n{me}"    # template; {me} = recruiter first name
    exclaim: bool = False           # tends to open/close with an exclamation
    formality: str = "neutral"      # formal | neutral | casual
    emoji: bool = False
    avg_sentence_words: float = 14.0
    samples: int = 0                # how many messages it was calibrated on

    def render_greeting(self, first_name: str) -> str:
        return self.greeting.format(first=first_name)

    def render_signoff(self, me: str) -> str:
        return self.signoff.format(me=me)


DEFAULT_STYLE = StyleProfile()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _signoff_line(text: str) -> str | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines[::-1]:
        if _SIGNOFF_RX.match(ln):
            return ln
    return None


def _sentence_lengths(text: str) -> list[int]:
    body = "\n".join(ln for ln in text.splitlines()
                     if not _GREETING_RX.match(ln.strip()) and not _SIGNOFF_RX.match(ln.strip()))
    return [len(s.split()) for s in re.split(r"(?<=[.?!])\s+|\n+", body) if s.split()]


def calibrate(messages: list[str], recruiter_name: str = "") -> StyleProfile:
    """Build a StyleProfile from the recruiter's own sent messages (5+ recommended)."""
    msgs = [m for m in messages if m and m.strip()]
    if not msgs:
        return StyleProfile(samples=0)

    greet_words: Counter[str] = Counter()
    exclaims = 0
    signoffs: Counter[str] = Counter()
    emoji_hits = 0
    formal_hits = contraction_hits = 0
    sent_lengths: list[int] = []

    for m in msgs:
        first = _first_line(m)
        gm = _GREETING_RX.match(first)
        if gm:
            word = gm.group(1).lower()
            greet_words[word] += 1
            if word in _FORMAL_GREETINGS:
                formal_hits += 1
        if "!" in first:
            exclaims += 1
        so = _signoff_line(m)
        if so:
            signoffs[_SIGNOFF_RX.match(so).group(0).strip().lower()] += 1
        if _EMOJI.search(m):
            emoji_hits += 1
        if _CONTRACTION.search(m):
            contraction_hits += 1
        sent_lengths += _sentence_lengths(m)

    n = len(msgs)
    greet_word = greet_words.most_common(1)[0][0] if greet_words else "hi"
    exclaim = exclaims >= (n + 1) // 2
    greet_display = {"hi there": "Hi there", "hi": "Hi", "hey there": "Hey there", "hey": "Hey",
                     "hello": "Hello", "dear": "Dear", "yo": "Hey", "good morning": "Hi",
                     "good afternoon": "Hi"}.get(greet_word, "Hi")
    greeting = f"{greet_display} {{first}}{'!' if exclaim else ','}"

    signoff_word = signoffs.most_common(1)[0][0] if signoffs else "best"
    signoff_display = {"best": "Best", "best regards": "Best regards", "regards": "Regards",
                       "cheers": "Cheers", "thanks": "Thanks", "thank you": "Thank you",
                       "thanks so much": "Thanks so much", "talk soon": "Talk soon", "warmly": "Warmly",
                       "all the best": "All the best", "speak soon": "Speak soon",
                       "sincerely": "Sincerely", "take care": "Take care"}.get(signoff_word, "Best")
    signoff = f"{signoff_display},\n{{me}}"

    casual = (greet_word in _CASUAL_GREETINGS) or exclaim or contraction_hits >= (n + 1) // 2
    formal = (formal_hits >= (n + 1) // 2) or (signoff_word in {"sincerely", "best regards", "regards"})
    formality = "formal" if formal and not casual else "casual" if casual and not formal else "neutral"
    avg_words = round(sum(sent_lengths) / len(sent_lengths), 1) if sent_lengths else 14.0

    return StyleProfile(greeting=greeting, signoff=signoff, exclaim=exclaim, formality=formality,
                        emoji=emoji_hits >= (n + 1) // 2, avg_sentence_words=avg_words, samples=n)


def style_guide(profile: StyleProfile) -> str:
    """A compact instruction block for the Claude composer."""
    bits = [f"- Greeting style: {profile.greeting.replace('{first}', '<first name>')}",
            f"- Sign-off: {profile.signoff.replace('{me}', '<your first name>').replace(chr(10), ' / ')}",
            f"- Register: {profile.formality}",
            f"- Exclamation marks: {'use sparingly, it fits their voice' if profile.exclaim else 'avoid'}",
            f"- Emoji: {'occasional, matching their style' if profile.emoji else 'none'}",
            f"- Typical sentence length: about {profile.avg_sentence_words:.0f} words"]
    return "Write in this recruiter's voice:\n" + "\n".join(bits)
