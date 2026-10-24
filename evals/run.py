"""Run the evaluation dataset against a Brain and print a scored report.

    python -m evals.run            # heuristic brain (offline)
    python -m evals.run --claude   # Claude brain (needs Anthropic credentials)

Exposed as evaluate(brain) so tests can assert a pass bar.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from autopilot.brain.base import Brain, ComposePlan, ConversationContext
from autopilot.brain.text import looks_automated
from autopilot.knowledge import KnowledgeBase
from autopilot.guardrails import unverified_answers
from autopilot.models import Candidate, Company, Direction, Job, Message, OfferedSlot, Recruiter
from evals.dataset import CASES, GROUNDING_CASES, GROUNDING_COMPANY_KB, GROUNDING_JOB_KB

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
_REC = Recruiter("r", "Sam Rivera", "sam@acme.com", "America/New_York")
_CAND = Candidate("a", "Ana Tester", "ana@example.com", timezone="America/New_York")


def _slots(n: int) -> list[OfferedSlot]:
    return [OfferedSlot(NOW + timedelta(days=d + 1, hours=1), NOW + timedelta(days=d + 1, hours=1, minutes=30))
            for d in range(n)]


def _context(body: str, headers: dict, sender: str, offered: int, kb: KnowledgeBase,
             has_meeting: bool) -> tuple[ConversationContext, Message]:
    msg = Message("m", "t", Direction.INBOUND, sender, "sam@acme.com", "Re: Platform Engineer", body, NOW,
                  headers=headers)
    meeting = None
    if has_meeting:
        from autopilot.models import Meeting
        meeting = Meeting(NOW + timedelta(days=2, hours=1), NOW + timedelta(days=2, hours=1, minutes=30), "evt")
    ctx = ConversationContext(_REC, _CAND, "Platform Engineer", "Acme", "", kb, [msg], msg, NOW,
                              "America/New_York", offered_slots=_slots(offered), meeting=meeting)
    return ctx, msg


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)

    def record(self, ok: bool, detail: str) -> None:
        self.total += 1
        if ok:
            self.correct += 1
        else:
            self.failures.append(detail)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0


def classification_report(brain: Brain, include_multilingual: bool = True) -> dict[str, FieldScore]:
    kb = KnowledgeBase(Company("c", "Acme"), Job("j", "Platform Engineer", "c", "r"))
    scores: dict[str, FieldScore] = {k: FieldScore() for k in
                                     ("interest", "has_questions", "scheduling", "needs_human", "unsubscribe",
                                      "auto_reply", "objections")}
    for case in CASES:
        tags = case.get("tags", set())
        if not include_multilingual and "multilingual" in tags:
            continue
        headers = case.get("headers", {})
        sender = case.get("sender", _CAND.email)
        expect = case["expect"]

        if "auto_reply" in expect:
            msg = Message("m", "t", Direction.INBOUND, sender, "x", "Re: Platform Engineer", case["body"], NOW,
                          headers=headers)
            got = looks_automated(msg)
            scores["auto_reply"].record(got == expect["auto_reply"], f"{case['id']}: auto={got}")
            if got:  # a filtered message never reaches analyze()
                continue

        ctx, _ = _context(case["body"], headers, sender, case.get("offered", 0), kb, "has_meeting" in tags)
        a = brain.analyze(ctx)
        if "interest" in expect:
            scores["interest"].record(a.interest.value == expect["interest"],
                                      f"{case['id']}: got {a.interest.value}, want {expect['interest']}")
        if "has_questions" in expect:
            got = bool(a.questions)
            scores["has_questions"].record(got == expect["has_questions"],
                                           f"{case['id']}: questions={a.questions}")
        if "scheduling" in expect:
            scores["scheduling"].record(a.scheduling.value == expect["scheduling"],
                                        f"{case['id']}: got {a.scheduling.value}, want {expect['scheduling']}")
        if "needs_human" in expect:
            scores["needs_human"].record(a.needs_human == expect["needs_human"],
                                         f"{case['id']}: needs_human={a.needs_human}")
        if "unsubscribe" in expect:
            scores["unsubscribe"].record(a.unsubscribe == expect["unsubscribe"],
                                         f"{case['id']}: unsubscribe={a.unsubscribe}")
        if "objections" in expect:
            want, got = set(expect["objections"]), set(a.objections)
            ok = want <= got if want else not got  # expected codes present; none expected -> none found
            scores["objections"].record(ok, f"{case['id']}: got {sorted(got)}, want ⊇ {sorted(want)}")
    return scores


def grounding_report(brain: Brain) -> FieldScore:
    kb = KnowledgeBase(Company("c", "Acme", knowledge=GROUNDING_COMPANY_KB),
                       Job("j", "Platform Engineer", "c", "r", knowledge=GROUNDING_JOB_KB))
    score = FieldScore()
    for case in GROUNDING_CASES:
        ctx, _ = _context(case["question"], {}, _CAND.email, 0, kb, False)
        q = case["question"]
        result = brain.compose(ctx, ComposePlan(kind="reply", questions=[q]))
        bad = unverified_answers(result, kb, [q], 0.7)
        answered = any(c.question == q and c.answered for c in result.answers) and q not in bad
        deferred = not answered
        body = result.body.lower()
        if case["answerable"]:
            ok = (not deferred) and case["must_include"].lower() in body
            score.record(ok, f"{case['id']}: answerable but deferred={deferred} / missing fact; body={body!r}")
        else:
            # Must defer AND must not have invented a figure in the body.
            import re
            invented = bool(re.search(r"\$\s?\d|\b\d{2,3}\s?k\b|\b\d+\s?%|\b\d+\s+(days|weeks)\b", body))
            ok = deferred and not invented
            score.record(ok, f"{case['id']}: unanswerable but deferred={deferred} invented={invented}; body={body!r}")
    return score


def evaluate(brain: Brain) -> dict:
    return {
        "classification": classification_report(brain, include_multilingual=True),
        "classification_core": classification_report(brain, include_multilingual=False),
        "grounding": grounding_report(brain),
    }


def _print(scores: dict[str, FieldScore], title: str) -> None:
    print(f"\n{title}")
    for name, s in scores.items():
        bar = "ok " if s.accuracy == 1.0 else "FAIL"
        print(f"  [{bar}] {name:14s} {s.correct}/{s.total}  ({s.accuracy:.0%})")
        for f in s.failures:
            print(f"        - {f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", action="store_true")
    args = parser.parse_args()
    if args.claude:
        from autopilot.brain.claude import ClaudeBrain
        brain: Brain = ClaudeBrain()
        label = "ClaudeBrain"
    else:
        from autopilot.brain import HeuristicBrain
        brain = HeuristicBrain()
        label = "HeuristicBrain"

    report = evaluate(brain)
    print(f"=== Evaluation: {label} ===")
    _print(report["classification"], "Classification (all cases, incl. multilingual)")
    _print({"grounding": report["grounding"]}, "Answer grounding")
    core = report["classification_core"]
    overall = sum(s.correct for s in core.values()) / max(1, sum(s.total for s in core.values()))
    print(f"\nCore classification accuracy (excl. multilingual): {overall:.1%}")
    print(f"Grounding accuracy: {report['grounding'].accuracy:.0%}")


if __name__ == "__main__":
    main()
