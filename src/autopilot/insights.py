"""Candidate-experience signals: objections, conversation warmth, and at-risk detection.

Pin's agent is purely transactional — it answers, books, nudges, archives. A great human
recruiter also reads the room: they notice a concern behind a question, feel a candidate
cooling off, and step in before a good candidate is lost. This module extracts those signals
deterministically (LLM optional) so the engine can act on them: route hard objections to a
human, warm the tone when a candidate cools, and raise an at-risk flag before silence sets in.

All functions are pure and offline-testable.
"""

from __future__ import annotations

import re

from autopilot.brain.text import strip_quoted
from autopilot.models import Message

# --- Objection taxonomy -----------------------------------------------------------------
# HARD objections need a human decision (money, competing situations). SOFT objections are
# concerns the agent can acknowledge and address from the knowledge base.
HARD_OBJECTIONS = {"comp_too_low", "counteroffer", "competing_offer"}
SOFT_OBJECTIONS = {"location", "seniority_mismatch", "timing", "role_scope", "company_stage", "workload"}

_OBJECTION_PATTERNS: dict[str, str] = {
    "comp_too_low": r"(too low|below market|under ?market|lowball|low-?ball|looking for (more|higher)|"
                    r"expect(ing|ed)? (more|higher)|(comp|salary|range|pay|base|the offer)\b[^.?!]{0,25}\b"
                    r"(low|light|short|lower than)|not (quite )?(enough|competitive)|more (money|comp))",
    "counteroffer": r"(counter ?offer|counter-?offer|my (current|existing) (company|employer|team|boss|manager)"
                    r"\b[^.?!]{0,20}(match|matched|matching|counter|countered|offered)|retention (bonus|offer|package)|"
                    r"they (just )?(matched|countered))",
    "competing_offer": r"(another offer|competing offer|other offers?|final (round|stage)s? (with|at) "
                       r"(another|other)|in process (with|at) (another|other)|weighing (a few|other|multiple))",
    "location": r"(too far|the commute|relocat|willing to move|only (do|want) (fully )?remote|remote only|"
                r"not .{0,15}(on-?site|in[- ]office|in the office)|don'?t want to (move|commute|relocate))",
    "seniority_mismatch": r"(over ?qualified|too (junior|senior) for me|step down|lateral move|same (level|title)|"
                          r"not (really )?a step up|the (title|level|seniority) is)",
    "timing": r"(bad timing|timing (isn'?t|is not) (great|right)|too busy right now|in the middle of|"
              r"just started|wrapping up|not (a good|the right) time)",
    "role_scope": r"(not (really )?what i (do|focus)|different (focus|area|stack|domain)|not my (area|wheelhouse)|"
                  r"the scope (seems|sounds|is)|more (ic|management|hands-on) than)",
    "company_stage": r"(too (early|risky|small)|startup risk|how much runway|still (pre-?revenue|pre-?seed)|"
                     r"stability|is (the company|it) (funded|profitable)|how (well )?funded)",
    "workload": r"(work[- ]life balance|the hours|burn ?out|on-?call|overworked|how many hours|crunch)",
}
_OBJECTION_RX = {code: re.compile(pat, re.IGNORECASE) for code, pat in _OBJECTION_PATTERNS.items()}

OBJECTION_LABELS = {
    "comp_too_low": "compensation", "counteroffer": "a counteroffer", "competing_offer": "a competing offer",
    "location": "location and work setup", "seniority_mismatch": "the role's level",
    "timing": "timing", "role_scope": "the role's focus", "company_stage": "the company's stage",
    "workload": "workload and work-life balance",
}

# --- Warmth cues ------------------------------------------------------------------------
_POSITIVE = re.compile(
    r"\b(great|excited|exciting|love|awesome|perfect|happy|glad|interested|keen|definitely|absolutely|"
    r"looking forward|sounds (good|great)|can'?t wait|thanks so much|appreciate)\b|!|:\)|😊|🙂|🎉", re.IGNORECASE)
_NEGATIVE = re.compile(
    r"\b(unfortunately|honestly|to be honest|not sure|hesitant|concerned|worried|but |however|"
    r"i guess|i suppose|maybe|we'?ll see|busy|swamped|no time|later|eventually)\b", re.IGNORECASE)


def warmth_score(text: str) -> float:
    """A rough enthusiasm score in [-1, 1] from lexical cues; used to detect a cooling trend."""
    body = strip_quoted(text)
    if not body:
        return 0.0
    pos = len(_POSITIVE.findall(body))
    neg = len(_NEGATIVE.findall(body))
    length = max(1, len(body))
    terseness = -0.3 if length < 40 else 0.0  # a clipped reply is a mild cooling signal
    raw = (pos - neg) / (pos + neg + 2) + terseness
    return max(-1.0, min(1.0, raw))


def detect_objections(text: str) -> list[str]:
    body = strip_quoted(text)
    return [code for code, rx in _OBJECTION_RX.items() if rx.search(body)]


def sentiment_trend(thread: list[Message], latest: Message) -> str:
    """Compare the latest inbound warmth to the prior inbound warmth: warming / steady / cooling."""
    from autopilot.models import Direction

    inbound = [m for m in thread if m.direction is Direction.INBOUND]
    latest_score = warmth_score(latest.body)
    prior = [m for m in inbound if m is not latest]
    if not prior:
        return "warming" if latest_score > 0.3 else "cooling" if latest_score < -0.2 else "steady"
    prev_score = warmth_score(prior[-1].body)
    delta = latest_score - prev_score
    if delta <= -0.35 or (latest_score < -0.2 and delta < 0):
        return "cooling"
    if delta >= 0.35 or (latest_score > 0.4 and delta >= 0):
        return "warming"
    return "steady"


def assess_at_risk(objections: list[str], sentiment: str, interest: str) -> tuple[bool, str | None]:
    """A candidate worth saving who may slip away without a human touch."""
    hard = [o for o in objections if o in HARD_OBJECTIONS]
    if hard:
        return True, f"raised {', '.join(OBJECTION_LABELS[o] for o in hard)}"
    if sentiment == "cooling" and interest != "not_interested":
        return True, "engagement is cooling"
    soft = [o for o in objections if o in SOFT_OBJECTIONS]
    if soft and interest != "interested":
        return True, f"unresolved concern about {OBJECTION_LABELS[soft[0]]}"
    return False, None
