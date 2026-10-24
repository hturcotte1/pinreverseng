from autopilot.brain.base import AnswerClaim, ComposeResult
from autopilot.guardrails import check_text, unverified_answers
from autopilot.knowledge import KnowledgeBase
from autopilot.models import Company, Job

KB = KnowledgeBase(Company("c", "Acme", knowledge="Hybrid in Austin."),
                   Job("j", "Eng", "c", "r", knowledge="Compensation: $150k-$170k base."))


def test_clean_text_passes():
    assert check_text("Hi Ana, the range is $150k-$170k base. Best, Sam", KB, KB.company) == []


def test_flags_placeholders_offers_protected_and_invented_numbers():
    issues = check_text("Hi {{first_name}}, we'd like to extend you an offer at $200k. How old are you?",
                        KB, KB.company)
    joined = " ".join(issues)
    assert "placeholder" in joined
    assert "offer" in joined
    assert "protected" in joined
    assert "$200k" in joined


def test_recruiter_answers_count_as_sources():
    assert check_text("There's a $5k learning stipend.", KB, KB.company, ["$5k learning stipend"]) == []


def test_confidential_company_name_blocked():
    company = Company("c", "Acme", hide_details=True)
    kb = KnowledgeBase(company, Job("j", "Eng", "c", "r"))
    assert "names a confidential client" in check_text("Acme is great.", kb, company)


def test_unverified_answers():
    result = ComposeResult("...", [
        AnswerClaim("pay?", True, ["J1"], 0.9),
        AnswerClaim("visa?", True, ["J99"], 0.9),  # cites a fact that doesn't exist
        AnswerClaim("remote?", True, ["C1"], 0.3),  # low confidence
    ])
    assert unverified_answers(result, KB, ["pay?", "visa?", "remote?", "team size?"], 0.7) == \
        {"visa?", "remote?", "team size?"}  # team size was silently skipped
