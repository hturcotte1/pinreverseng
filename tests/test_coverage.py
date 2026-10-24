from datetime import datetime, timedelta, timezone

from autopilot.coverage import (
    answer_coverage,
    find_conflicts,
    gap_checklist,
    readiness_report,
    stale_facts,
)
from autopilot.knowledge import Fact, KnowledgeBase
from autopilot.models import Company, Job

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)

RICH_COMPANY = "Hybrid, 3 days a week in the SF office.\nBenefits: medical, dental, 401k 4% match.\nFounded 2019, Series B.\n"
RICH_JOB = ("Compensation: $185k-$215k base plus equity.\n"
            "Interview process: recruiter screen, technical round, onsite.\n"
            "Team: reports to the Director of Platform Eng; team of 7.\n"
            "Tech stack: Go, Kafka, Postgres.\n"
            "Visa: we do not offer sponsorship.\n"
            "PTO: unlimited, 3 weeks typical.\n"
            "Relocation: up to $10k support.\n")


def _kb(company_k="", job_k="", learned=None):
    return KnowledgeBase(Company("c", "Acme", knowledge=company_k),
                         Job("j", "Platform Engineer", "c", "r", knowledge=job_k), learned)


def test_coverage_is_high_for_a_rich_kb_and_low_for_an_empty_one():
    rich = answer_coverage(_kb(RICH_COMPANY, RICH_JOB))
    assert rich.score >= 0.75 and rich.ready is (len(rich.uncovered) == 0)
    empty = answer_coverage(_kb())
    assert empty.score == 0.0 and len(empty.uncovered) == len(rich.covered) + len(rich.uncovered)


def test_gap_checklist_names_missing_topics():
    kb = _kb("", "Compensation: $185k-$215k base.\n")  # only comp present
    gaps = gap_checklist(kb)
    assert any("interview process" in g.lower() for g in gaps)
    assert any("salary" in g.lower() for g in gaps) is False  # comp is covered, so not a gap


def test_find_conflicts_detects_money_and_yesno_contradictions():
    kb = _kb("", "Compensation: $185k-$215k base.\nComp for this req is actually $150k-$170k.\n"
                 "Visa: we do not sponsor.\nWe can sponsor H-1B transfers for strong candidates.\n")
    conflicts = find_conflicts(kb)
    assert any("ompensation" in c for c in conflicts)
    assert any("Visa" in c for c in conflicts)


def test_no_false_conflict_when_consistent():
    assert find_conflicts(_kb(RICH_COMPANY, RICH_JOB)) == []


def test_stale_facts_flags_old_learned_volatile_facts():
    learned_old = Fact(id="L1", text="Base is $190k.", scope="job", source="learned",
                       learned_from="comp?", created_at=NOW - timedelta(days=200))
    learned_fresh = Fact(id="L2", text="We offer dental.", scope="company", source="learned",
                         learned_from="benefits?", created_at=NOW - timedelta(days=5))
    kb = _kb("", "", learned=[learned_old, learned_fresh])
    stale = stale_facts(kb, NOW, max_age_days=120)
    assert any("L1" in s for s in stale) and not any("L2" in s for s in stale)


def test_readiness_report_shape():
    r = readiness_report(_kb(RICH_COMPANY, RICH_JOB), NOW)
    assert r["ready_for_outreach"] is True
    assert r["coverage_score"] >= 0.75 and r["conflicts"] == []
    blocked = readiness_report(_kb("", "Compensation: $185k.\nComp is $150k.\n"), NOW)
    assert blocked["ready_for_outreach"] is False  # conflict blocks readiness
