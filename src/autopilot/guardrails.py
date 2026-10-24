"""Checks that sit between the model and the outside world.

Two kinds of result:
* blocking issues - the text must not go out unreviewed (it becomes a draft with warnings);
* auto-send eligibility - whether policy lets this particular message send itself.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from autopilot.brain.base import ComposeResult
from autopilot.knowledge import KnowledgeBase
from autopilot.models import Card, Company, Stage
from autopilot.settings import AnswerMode, AutomationSettings

PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}|\[(first ?name|last ?name|name|company|candidate|job title)\]", re.I)
PROTECTED = re.compile(
    r"how old are you|your age|date of birth|are you (married|single|pregnant)|do you have (kids|children)|"
    r"plan(ning)? to have (kids|children)|your (religion|nationality|ethnicity|race|sexual orientation|"
    r"gender identity|disability)|where are you (originally )?from|what church", re.I)
OFFER_LANGUAGE = re.compile(
    r"(extend|make|making|extending) (you )?(an|the) offer|you('re| are) hired|the (job|role) is yours|"
    r"guarantee(d)? (you )?(an? )?(offer|the role|the job)", re.I)
FIGURE = re.compile(r"\$\s?\d[\d,.]*\s?[kKmM]?|\b\d{2,3}\s?[kK]\b|\b\d+(\.\d+)?\s?%")


def _norm_figure(s: str) -> str:
    return re.sub(r"[\s,$]", "", s.lower())


def check_text(body: str, kb: KnowledgeBase, company: Company, extra_sources: list[str] | None = None) -> list[str]:
    issues: list[str] = []
    if PLACEHOLDER.search(body):
        issues.append("unfilled template placeholder")
    if company.hide_details and re.search(re.escape(company.name), body, re.I):
        issues.append("names a confidential client")
    if PROTECTED.search(body):
        issues.append("asks about a protected characteristic")
    if OFFER_LANGUAGE.search(body):
        issues.append("implies a job offer")
    sources = " ".join([f.text for f in kb.facts] + (extra_sources or []))
    known = {_norm_figure(m.group(0)) for m in FIGURE.finditer(sources)}
    for m in FIGURE.finditer(body):
        if _norm_figure(m.group(0)) not in known:
            issues.append(f"figure '{m.group(0).strip()}' not found in knowledge base")
    if len(body) > 2500:
        issues.append("message too long")
    return issues


def unverified_answers(result: ComposeResult, kb: KnowledgeBase, questions: list[str], min_confidence: float,
                       allow_recruiter: bool = False) -> set[str]:
    """Questions the model claims to have answered without valid, confident citations."""
    bad: set[str] = set()
    claimed = {c.question: c for c in result.answers}
    for c in result.answers:
        if not c.answered:
            continue
        cited = [fid for fid in c.fact_ids if kb.get(fid) or (allow_recruiter and fid == "recruiter")]
        if not cited or c.confidence < min_confidence:
            bad.add(c.question)
    # A question the model silently skipped is treated as unanswered, not verified.
    return bad | {q for q in questions if q not in claimed and not any(_same(q, k) for k in claimed)}


def _same(a: str, b: str) -> bool:
    return a.strip().lower().rstrip("?") == b.strip().lower().rstrip("?")


def auto_send_blockers(card: Card, settings: AutomationSettings, now: datetime, agent_sends_last_day: int,
                       degraded: bool) -> list[str]:
    reasons: list[str] = []
    if settings.answer_mode is not AnswerMode.AUTO:
        reasons.append("answer mode is 'create drafts'")
    if degraded:
        reasons.append("model unavailable; offline fallback wrote this")
    if card.stage is Stage.HIRED or card.paused:
        reasons.append("conversation is with the recruiter")
    if card.last_human_outbound_at and now - card.last_human_outbound_at < timedelta(hours=settings.human_takeover_hours):
        reasons.append("recruiter wrote in this thread recently")
    if agent_sends_last_day >= settings.max_agent_replies_per_day:
        reasons.append("daily agent reply cap reached for this thread")
    return reasons
