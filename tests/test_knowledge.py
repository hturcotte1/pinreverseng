from datetime import datetime, timezone

from autopilot.knowledge import KnowledgeBase, LearnedFacts, split_facts, topics_of
from autopilot.models import Company, Job


def test_split_facts_prefixes_headings_onto_bullets():
    text = "Compensation:\n- $150k base\n- 10% bonus\n\nFully remote within the US."
    assert split_facts(text) == ["Compensation: $150k base", "Compensation: 10% bonus", "Fully remote within the US."]


def test_topics():
    assert topics_of("What's the salary range?") == {"compensation"}
    assert "visa" in topics_of("Do you sponsor H-1B visas?")
    assert "location" in topics_of("Is this remote or hybrid?")


def test_search_ranks_relevant_fact_first():
    kb = KnowledgeBase(Company("c", "Acme", knowledge="Hybrid, 3 days in office."),
                       Job("j", "Eng", "c", "r", knowledge="Compensation: $150k-$170k base.\nInterview: 3 rounds."))
    fact, score = kb.search("What does the job pay?")[0]
    assert fact.id == "J1" and score >= 0.45
    assert kb.search("Is it remote?")[0][0].id == "C1"


def test_hidden_company_is_redacted_in_render():
    kb = KnowledgeBase(Company("c", "Acme", hide_details=True, knowledge="Acme is Series B."), Job("j", "Eng", "c", "r"))
    assert "Acme" not in kb.render()
    assert "our client" in kb.render()


def test_learned_fact_replaces_older_answer_to_same_question():
    learned = LearnedFacts()
    job = Job("j", "Eng", "c", "r")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    learned.add(job, "No sponsorship.", "Do you sponsor visas?", "job", now)
    learned.add(job, "We now sponsor H-1B transfers.", "Do you sponsor visas for this role?", "job", now)
    facts = learned.for_job(job)
    assert [f.text for f in facts] == ["We now sponsor H-1B transfers."]


def test_company_scoped_learning_is_shared_across_jobs():
    learned = LearnedFacts()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    learned.add(Job("j1", "A", "c", "r"), "16 weeks parental leave.", "Parental leave?", "company", now)
    assert learned.for_job(Job("j2", "B", "c", "r"))[0].text == "16 weeks parental leave."
