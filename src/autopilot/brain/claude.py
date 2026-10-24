"""Claude-backed brain using structured outputs.

Each call returns schema-constrained JSON. Refusals, truncation and API errors fall
back to the offline brain with ``degraded=True`` so the engine downgrades to drafts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from autopilot.brain.base import (
    AnswerClaim,
    Brain,
    ComposePlan,
    ComposeResult,
    ConversationContext,
    DistilledFact,
    ReplyAnalysis,
    SchedulingIntent,
)
from autopilot.brain.heuristic import HeuristicBrain
from autopilot.brain.prompts import (
    ANALYSIS_SCHEMA,
    ANALYZE_SYSTEM,
    COMPOSE_SCHEMA,
    COMPOSE_SYSTEM,
    DISTILL_SCHEMA,
    DISTILL_SYSTEM,
)
from autopilot.brain.text import strip_quoted
from autopilot.models import Direction, Interest
from autopilot.scheduling import format_slot

try:  # the SDK is an optional extra; the offline brain never needs it
    import anthropic

    _API_ERRORS: tuple[type[Exception], ...] = (anthropic.RateLimitError, anthropic.APIStatusError,
                                                anthropic.APIConnectionError)
except ImportError:  # pragma: no cover - exercised only without the extra installed
    anthropic = None
    _API_ERRORS = ()

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class BrainUnavailable(RuntimeError):
    pass


class ClaudeBrain:
    def __init__(self, client: Any = None, model: str = DEFAULT_MODEL, analyze_effort: str = "low",
                 compose_effort: str = "medium", fallback: Brain | None = None):
        if client is None:
            if anthropic is None:
                raise RuntimeError("install the 'claude' extra: pip install 'autopilot-cleanroom[claude]'")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.analyze_effort = analyze_effort
        self.compose_effort = compose_effort
        self.fallback = fallback or HeuristicBrain()

    # -- transport ----------------------------------------------------------------------
    def _call(self, system: str, blocks: list[dict], schema: dict, effort: str) -> dict:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": blocks}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except _API_ERRORS as exc:
            raise BrainUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if response.stop_reason == "refusal":
            raise BrainUnavailable(f"model declined (category={getattr(response.stop_details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise BrainUnavailable("response truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise BrainUnavailable("no text block in response")
        return json.loads(text)

    @staticmethod
    def _job_block(ctx: ConversationContext) -> dict:
        text = (
            f"<job>\nTitle: {ctx.job_title}\nCOMPANY DISPLAY NAME: {ctx.company_display_name}\n"
            f"Recruiter: {ctx.recruiter.name}\nDescription:\n{ctx.kb.redact(ctx.job_description) or '(none)'}\n</job>\n"
            f"<knowledge_base>\n{ctx.kb.render()}\n</knowledge_base>"
        )
        return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}

    @staticmethod
    def _conversation_block(ctx: ConversationContext, extra: str = "") -> dict:
        slots = "\n".join(
            f"{i}: {format_slot(s.start, ctx.candidate_tz)} ({s.start.isoformat()})"
            for i, s in enumerate(ctx.offered_slots)
        ) or "(none)"
        meeting = (f"{format_slot(ctx.meeting.start, ctx.candidate_tz)} ({ctx.meeting.start.isoformat()})"
                   if ctx.meeting else "(none)")
        thread = "\n\n".join(
            f"[{m.sent_at.isoformat()}] {'RECRUITER' if m.direction is Direction.OUTBOUND else 'CANDIDATE'}:\n"
            f"{strip_quoted(m.body)}"
            for m in ctx.thread if m is not ctx.latest
        ) or "(no earlier messages)"
        latest = strip_quoted(ctx.latest.body) if ctx.latest else ""
        text = (
            f"NOW: {ctx.now.isoformat()}\nCandidate: {ctx.candidate.name}\n"
            f"Candidate timezone: {ctx.candidate_tz}\n"
            f"<offered_slots>\n{slots}\n</offered_slots>\n<meeting>{meeting}</meeting>\n"
            f"<thread>\n{thread}\n</thread>\n"
            f"<candidate_email>\n{latest}\n</candidate_email>"
        )
        return {"type": "text", "text": text + extra}

    # -- Brain protocol -----------------------------------------------------------------
    def analyze(self, ctx: ConversationContext) -> ReplyAnalysis:
        try:
            data = self._call(ANALYZE_SYSTEM, [self._job_block(ctx), self._conversation_block(ctx)],
                              ANALYSIS_SCHEMA, self.analyze_effort)
            return parse_analysis(data, n_slots=len(ctx.offered_slots))
        except (BrainUnavailable, ValueError, KeyError) as exc:
            log.warning("analyze fell back to heuristics: %s", exc)
            result = self.fallback.analyze(ctx)
            result.degraded = True
            return result

    def compose(self, ctx: ConversationContext, plan: ComposePlan) -> ComposeResult:
        plan_json = asdict(plan)
        plan_json["must_defer"] = sorted(plan_json.pop("force_defer"))
        extra = (f"\n<plan>\n{json.dumps(plan_json, indent=1, sort_keys=True)}\n</plan>\n"
                 f"Recruiter first name: {ctx.recruiter.first_name}\n"
                 f"Candidate first name: {ctx.candidate.first_name}")
        try:
            data = self._call(COMPOSE_SYSTEM, [self._job_block(ctx), self._conversation_block(ctx, extra)],
                              COMPOSE_SCHEMA, self.compose_effort)
            return ComposeResult(
                body=data["body"].strip(),
                answers=[AnswerClaim(a["question"], bool(a["answered"]), list(a["fact_ids"]),
                                     max(0.0, min(1.0, float(a["confidence"])))) for a in data["answers"]],
                latest_activity=data["latest_activity"].strip(),
                open_item=(data["open_item"] or None),
            )
        except (BrainUnavailable, ValueError, KeyError, TypeError) as exc:
            log.warning("compose fell back to heuristics: %s", exc)
            result = self.fallback.compose(ctx, plan)
            result.degraded = True
            return result

    def distill_fact(self, ctx: ConversationContext, question: str, answer: str) -> DistilledFact:
        block = {"type": "text", "text": f"<question>{question}</question>\n<recruiter_answer>{answer}"
                                         f"</recruiter_answer>"}
        try:
            data = self._call(DISTILL_SYSTEM, [self._job_block(ctx), block], DISTILL_SCHEMA, "low")
            return DistilledFact(data["fact"], data["scope"], bool(data["shareable"]))
        except (BrainUnavailable, ValueError, KeyError) as exc:
            log.warning("distill fell back to heuristics: %s", exc)
            return self.fallback.distill_fact(ctx, question, answer)


def parse_analysis(data: dict, n_slots: int) -> ReplyAnalysis:
    windows = []
    for w in data["proposed_windows"]:
        try:
            start, end = datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"])
        except ValueError:
            continue
        if start.tzinfo and end.tzinfo and end > start:
            windows.append((start, end))
    idx = data["accepted_slot_index"]
    follow_up = None
    if data["follow_up_on"]:
        try:
            follow_up = date.fromisoformat(data["follow_up_on"][:10])
        except ValueError:
            follow_up = None
    scheduling = SchedulingIntent(data["scheduling"])
    if scheduling is SchedulingIntent.ACCEPTS_SLOT and not (isinstance(idx, int) and 0 <= idx < n_slots):
        scheduling, idx = SchedulingIntent.NONE, None  # model picked a slot we never offered
    return ReplyAnalysis(
        is_auto_reply=bool(data["is_auto_reply"]),
        interest=Interest(data["interest"]),
        not_interested_reason=data["not_interested_reason"],
        questions=[q.strip() for q in data["questions"] if q.strip()],
        scheduling=scheduling,
        accepted_slot_index=idx if scheduling is SchedulingIntent.ACCEPTS_SLOT else None,
        proposed_windows=windows,
        candidate_timezone=data["candidate_timezone"],
        offer_status=data["offer_status"],
        unsubscribe=bool(data["unsubscribe"]),
        needs_human=bool(data["needs_human"]),
        needs_human_reason=data["needs_human_reason"],
        dealbreaker_conflicts=list(data["dealbreaker_conflicts"]),
        follow_up_on=follow_up,
        tone=data["tone"],
        objections=list(data.get("objections", [])),
        sentiment=data.get("sentiment", "steady"),
        at_risk=bool(data.get("at_risk", False)),
        at_risk_reason=data.get("at_risk_reason"),
        summary=data["summary"],
    )
