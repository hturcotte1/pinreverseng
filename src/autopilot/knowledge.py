"""Knowledge base: company-wide + job-level facts, plus facts learned from recruiter answers.

Recruiters type free text; we split it into addressable facts so every answer the
agent gives can cite the fact ids it relied on (and guardrails can verify them).
Per-job knowledge is small, so the LLM path puts the whole KB in the prompt; the
lexical scorer here backs the offline brain and ranks facts for display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from autopilot.models import Company, Job

TOPIC_TERMS: dict[str, set[str]] = {
    "compensation": {"salary", "pay", "comp", "compensation", "range", "base", "bonus", "equity",
                     "stock", "options", "rsu", "rsus", "ote", "commission", "money", "paying"},
    "benefits": {"benefits", "perks", "perk", "package"},
    "health": {"health", "insurance", "dental", "medical", "vision", "healthcare"},
    "retirement": {"401k", "pension", "retirement", "match"},
    "pto": {"pto", "vacation", "leave", "parental", "maternity", "paternity", "holidays", "sabbatical",
            "time-off"},
    "location": {"remote", "hybrid", "office", "onsite", "on-site", "location", "based", "in-office",
                 "commute", "wfh", "where"},
    "relocation": {"relocate", "relocation", "relocating", "moving", "move"},
    "process": {"interview", "interviews", "process", "rounds", "stages", "steps", "timeline",
                "loop", "take-home", "assessment"},
    "team": {"team", "report", "reports", "reporting", "manager", "size", "engineers"},
    "visa": {"visa", "sponsor", "sponsorship", "h1b", "h-1b", "immigration", "authorization", "permit"},
    "company": {"company", "funding", "founded", "series", "product", "customers", "mission",
                "startup", "revenue", "raised"},
    "role": {"responsibilities", "stack", "tech", "technologies", "day-to-day", "projects",
             "scope", "level", "seniority", "languages"},
    "travel": {"travel", "trips", "travelling", "traveling"},
    "start_date": {"start", "notice", "asap"},
}

_STOP = {"the", "a", "an", "is", "are", "be", "to", "of", "and", "or", "for", "in", "on", "it",
         "this", "that", "do", "does", "you", "your", "we", "our", "i", "me", "my", "what", "how",
         "can", "will", "would", "could", "there", "any", "with", "at", "as", "about", "also",
         "if", "so", "just", "like", "have", "has", "role", "job", "position"}


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9$][a-z0-9$\-]*", text.lower())
    return {w for w in words if w not in _STOP}


def topics_of(text: str) -> set[str]:
    toks = tokenize(text)
    return {topic for topic, terms in TOPIC_TERMS.items() if toks & terms}


@dataclass
class Fact:
    id: str
    text: str
    scope: str  # "company" | "job"
    source: str = "recruiter"  # "recruiter" | "learned"
    learned_from: str | None = None
    created_at: datetime | None = None
    topics: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.topics:
            self.topics = topics_of(self.text + " " + (self.learned_from or ""))


def split_facts(text: str) -> list[str]:
    """Turn a free-text knowledge box into one fact per bullet/line.

    A line ending with ':' is treated as a heading and prefixed onto the bullets
    beneath it, so "Compensation:\\n- $180-210k base" becomes one self-contained fact.
    """
    facts: list[str] = []
    heading: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            heading = None
            continue
        bullet = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", line)
        body = bullet.group(1).strip() if bullet else line
        if body.endswith(":") and len(body) < 60:
            heading = body[:-1].strip()
            continue
        facts.append(f"{heading}: {body}" if heading and bullet else body)
    return facts


class KnowledgeBase:
    """Facts visible to the agent for one job (job facts + company facts + learned facts)."""

    def __init__(self, company: Company, job: Job, learned: list[Fact] | None = None):
        self.company = company
        self.job = job
        self._facts: list[Fact] = []
        for i, text in enumerate(split_facts(company.knowledge), 1):
            self._facts.append(Fact(id=f"C{i}", text=text, scope="company"))
        for i, text in enumerate(split_facts(job.knowledge), 1):
            self._facts.append(Fact(id=f"J{i}", text=text, scope="job"))
        self._facts.extend(learned or [])

    @property
    def facts(self) -> list[Fact]:
        return list(self._facts)

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self._facts if f.id == fact_id), None)

    def redact(self, text: str) -> str:
        if not self.company.hide_details:
            return text
        return re.sub(re.escape(self.company.name), "our client", text, flags=re.IGNORECASE)

    def render(self) -> str:
        """Prompt-ready listing; company name removed for confidential searches."""
        if not self._facts:
            return "(knowledge base is empty)"
        return "\n".join(f"[{f.id}] {self.redact(f.text)}" for f in self._facts)

    def can_answer(self, question: str, threshold: float = 0.45) -> tuple[bool, "Fact | None", float]:
        """Whether the KB supports answering a question — the same rule the composer uses.

        Shared so the pre-outreach coverage report and the live composer never disagree about
        what counts as answerable.
        """
        hits = self.search(question, k=1)
        if not hits:
            return False, None, 0.0
        fact, score = hits[0]
        ok = score >= threshold and bool(fact.topics & topics_of(question))
        return ok, (fact if ok else None), score

    def search(self, query: str, k: int = 3) -> list[tuple[Fact, float]]:
        q_topics = topics_of(query)
        q_tokens = tokenize(query)
        scored: list[tuple[Fact, float]] = []
        for fact in self._facts:
            topic_score = len(q_topics & fact.topics) / len(q_topics) if q_topics else 0.0
            f_tokens = tokenize(fact.text + " " + (fact.learned_from or ""))
            token_score = len(q_tokens & f_tokens) / max(1, len(q_tokens))
            score = 0.65 * topic_score + 0.35 * min(1.0, token_score * 1.5)
            if score > 0:
                scored.append((fact, round(score, 3)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


class LearnedFacts:
    """Facts added by 'Allow learning', keyed by job id or company id."""

    def __init__(self) -> None:
        self._by_scope: dict[tuple[str, str], list[Fact]] = {}
        self._counter = 0

    def for_job(self, job: Job) -> list[Fact]:
        return self._by_scope.get(("company", job.company_id), []) + self._by_scope.get(("job", job.id), [])

    def add(self, job: Job, text: str, question: str, scope: str, at: datetime) -> Fact:
        key = ("company", job.company_id) if scope == "company" else ("job", job.id)
        bucket = self._by_scope.setdefault(key, [])
        new_tokens = tokenize(question)
        for existing in list(bucket):
            old_tokens = tokenize(existing.learned_from or "")
            overlap = len(new_tokens & old_tokens) / max(1, len(new_tokens | old_tokens))
            if overlap >= 0.6:
                bucket.remove(existing)  # newer answer to the same question wins
        self._counter += 1
        fact = Fact(id=f"L{self._counter}", text=text, scope=scope, source="learned",
                    learned_from=question, created_at=at)
        bucket.append(fact)
        return fact
