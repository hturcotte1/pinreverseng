"""Knowledge-base intelligence: pre-outreach answer coverage, conflicts, and staleness.

Pin's knowledge base is a plain text box you fill in and hope is enough; the agent only
discovers a gap when a candidate asks something it can't answer and it has to interrupt the
recruiter on Slack. This module makes that proactive: before a single message goes out, it
reports which questions candidates reliably ask that the KB can already answer, which it can't
(so the recruiter fills them first), whether any facts contradict each other, and which learned
facts are stale enough to re-confirm. All pure and offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from autopilot.knowledge import KnowledgeBase, tokenize, topics_of

# Questions candidates reliably ask (Pin's own benchmark: comp, interview steps, work setup lead).
CANONICAL_QUESTIONS: list[tuple[str, str]] = [
    ("compensation", "What is the salary range for this role?"),
    ("location", "Is the role remote, hybrid, or in the office?"),
    ("process", "What does the interview process look like?"),
    ("team", "Who would I report to and how big is the team?"),
    ("role", "What would I actually be working on, and what's the tech stack?"),
    ("visa", "Do you offer visa sponsorship?"),
    ("benefits", "What benefits do you offer?"),
    ("pto", "What is the PTO and leave policy?"),
    ("start_date", "When would this role start?"),
    ("company", "How is the company funded and what stage is it at?"),
    ("relocation", "Is there relocation support?"),
    ("workload", "What are the working hours and on-call expectations?"),
]

# Topics whose facts go stale and should be re-confirmed periodically.
VOLATILE_TOPICS = {"compensation", "benefits", "health", "retirement", "pto", "company"}
_NEGATION = re.compile(r"\b(no|not|without|don'?t|doesn'?t|cannot|can'?t|unable|isn'?t|won'?t|never|"
                       r"unfortunately)\b", re.IGNORECASE)
_MONEY = re.compile(r"\$\s?\d[\d,]*\s?[kKmM]?(?:\s?[-–to]+\s?\$?\d[\d,]*\s?[kKmM]?)?")


@dataclass
class Coverage:
    covered: list[tuple[str, str]] = field(default_factory=list)   # (question, fact_id)
    uncovered: list[str] = field(default_factory=list)            # questions the KB can't answer
    score: float = 0.0                                           # fraction covered

    @property
    def ready(self) -> bool:
        return not self.uncovered


def answer_coverage(kb: KnowledgeBase, questions: list[tuple[str, str]] | None = None) -> Coverage:
    qs = questions or CANONICAL_QUESTIONS
    cov = Coverage()
    for _, question in qs:
        ok, fact, _ = kb.can_answer(question)
        if ok and fact is not None:
            cov.covered.append((question, fact.id))
        else:
            cov.uncovered.append(question)
    cov.score = len(cov.covered) / len(qs) if qs else 1.0
    return cov


def gap_checklist(kb: KnowledgeBase) -> list[str]:
    """Human-readable prompts for the recruiter to fill before enabling outreach."""
    return [f"Candidates usually ask: \"{q}\" — add this to the knowledge base so Autopilot can answer it."
            for q in answer_coverage(kb).uncovered]


def find_conflicts(kb: KnowledgeBase) -> list[str]:
    """Facts that contradict each other — surfaced for the recruiter, never auto-resolved."""
    conflicts: list[str] = []
    facts = kb.facts
    by_topic: dict[str, list] = {}
    for f in facts:
        for t in f.topics:
            by_topic.setdefault(t, []).append(f)

    # Compensation: two facts citing different money figures.
    comp = by_topic.get("compensation", [])
    money = {f.id: set(m.group(0).replace(" ", "") for m in _MONEY.finditer(f.text)) for f in comp}
    money = {fid: v for fid, v in money.items() if v}
    ids = list(money)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if not (money[ids[i]] & money[ids[j]]):
                conflicts.append(f"Compensation facts [{ids[i]}] and [{ids[j]}] cite different figures "
                                 f"({', '.join(money[ids[i]])} vs {', '.join(money[ids[j]])}) — verify which is current.")

    # Yes/no contradictions within a topic (e.g. visa sponsorship stated both ways).
    for topic in ("visa", "location", "relocation"):
        group = by_topic.get(topic, [])
        if len(group) < 2:
            continue
        pos = [f for f in group if not _NEGATION.search(f.text)]
        neg = [f for f in group if _NEGATION.search(f.text)]
        if pos and neg:
            conflicts.append(f"{topic.capitalize()} facts disagree: [{neg[0].id}] is stated negatively "
                             f"while [{pos[0].id}] is stated positively — confirm the real policy.")
    return conflicts


def stale_facts(kb: KnowledgeBase, now: datetime, max_age_days: int = 120) -> list[str]:
    """Learned facts in volatile topics old enough to re-confirm."""
    out = []
    for f in kb.facts:
        if f.source == "learned" and f.created_at and f.topics & VOLATILE_TOPICS:
            age = (now - f.created_at).days
            if age >= max_age_days:
                out.append(f"[{f.id}] learned {age} days ago ({', '.join(sorted(f.topics & VOLATILE_TOPICS))}) "
                           "— re-confirm it's still accurate.")
    return out


def readiness_report(kb: KnowledgeBase, now: datetime) -> dict:
    cov = answer_coverage(kb)
    return {
        "coverage_score": round(cov.score, 2),
        "answerable": len(cov.covered),
        "gaps": gap_checklist(kb),
        "conflicts": find_conflicts(kb),
        "stale": stale_facts(kb, now),
        "ready_for_outreach": cov.score >= 0.6 and not find_conflicts(kb),
    }
