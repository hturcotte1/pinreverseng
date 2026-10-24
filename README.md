# pinreverseng

A public-source teardown of **Pin's Autopilot** (an AI recruiting reply agent launched
2026-10-23), plus a **clean-room reimplementation** of the same behaviour.

* [`docs/TEARDOWN.md`](docs/TEARDOWN.md): what Autopilot is, its complete documented feature
  surface, how it grew out of earlier Pin releases, the vendor stack, and an inferred
  architecture with confidence levels. Every claim is sourced.
* [`docs/RESEARCH_FINDINGS.md`](docs/RESEARCH_FINDINGS.md): extended public-source research —
  founder lineage (Interseller), tech stack, the MCP object model, published outreach-cadence data,
  and the contradictions between sources, each with a source and confidence level.
* [`docs/BLACKBOX_TEST_PLAN.md`](docs/BLACKBOX_TEST_PLAN.md): 24 experiments to settle the open
  questions by using the product normally on your own account.
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): how this implementation works.
* [`docs/METHOD_AND_BOUNDARIES.md`](docs/METHOD_AND_BOUNDARIES.md): what was and was not done, and why.
* [`docs/BEYOND_PIN.md`](docs/BEYOND_PIN.md): the capabilities that take this past Pin's reactive
  Autopilot — candidate-experience intelligence, a groundedness/experience gate, a self-maintaining
  knowledge base, voice calibration, an interview concierge, a conversion ledger, flight plans,
  shadow mode, and an autonomy governor — each with its safety rule.

**Method.** Only publicly available material was used. No login, decompilation, API probing or
scraping. The repository contains no Pin code or content and is not affiliated with Pin.

## The implementation

`src/autopilot` is a working reply agent:
* reads candidate replies;
* moves them through a Replied → Qualifying → Interviewing → Hired/Archived pipeline;
* answers questions from a knowledge base, citing the facts it used;
* escalates unknowns to Slack and learns the recruiter's answers;
* offers or books interview slots against a calendar;
* nudges and archives quiet candidates;
* drafts or sends after a human-like delay.

Two brains plug into the same engine:
* `HeuristicBrain`: offline and deterministic. Used by tests and the demo, and as the fallback
  when the model is unavailable.
* `ClaudeBrain`: Claude (`claude-opus-5-5`) with JSON-schema structured outputs, prompt caching
  and server-side refusal fallback.

```bash
pip install -e '.[dev]'           # add ,claude for the Claude brain
python -m pytest                  # 78 tests
python -m autopilot.demo          # simulated two weeks: 6 candidates, Slack Q&A, learning, booking, nudges
python -m autopilot.demo --drafts # same, in review-every-draft mode
python -m autopilot.demo --claude # needs Anthropic credentials (ANTHROPIC_API_KEY or `ant auth login`)
python -m evals.run               # scored evaluation of the reply agent (offline, heuristic brain)
python -m evals.run --claude      # same evaluation against the Claude brain
```

The Claude path is unit-tested against a fake client: request shape, schema parsing, refusal
fallback. It has not yet been exercised against the live API.

## Evaluation

`evals/` scores the agent offline against a labelled dataset (`evals/dataset.py`):

* **classification** — per-field accuracy of reply understanding (interest, questions,
  scheduling intent, needs-human, unsubscribe, auto-reply);
* **answer grounding** — the agent must answer only what the knowledge base supports and defer
  everything else, never inventing a figure;
* **injection robustness** — jailbreak-style emails must not cause an auto-send or leak the
  knowledge base (see `tests/test_evals.py`).

The offline heuristic brain scores 100% on core classification and grounding; multilingual
replies are a known heuristic gap (the Claude brain covers them) and are reported but excluded
from the heuristic pass bar.

## Production adapters

`src/autopilot/integrations/` has reference adapters for real deployments: `gmail.py`,
`graph.py` (Outlook / Microsoft 365), `google_calendar.py`, and a Slack Events ingress
(`slack.py::SlackEventsIngress`). Their pure parts — MIME threading, message parsing,
free/busy parsing, webhook signature + routing — are tested; the thin API wrappers need
credentials and are marked reference-only.

## Layout
```
src/autopilot/
  engine.py         orchestration: inbound -> analyze -> policy -> compose -> verify -> deliver; timers
  brain/            analyze / compose / distil_fact interface; heuristic + Claude implementations; prompts
  knowledge.py      company/job/learned facts with ids; can_answer
  insights.py       objection / sentiment / at-risk signals (candidate-experience layer)
  coverage.py       pre-outreach KB coverage, conflict + staleness detection
  voice.py          per-recruiter voice calibration (StyleProfile)
  scheduling.py     slot finding, labels, US holidays
  guardrails.py     citation verification, text checks, candidate-experience gate, auto-send eligibility
  pipeline.py       stage transition rules
  ledger.py         conversion funnel + per-card leak attribution
  flightplan.py     inspectable record of every outbound decision
  shadow.py         shadow-mode agreement scoring
  governor.py       per-job autonomy tier + sticky circuit breaker
  settings.py       Outreach-Automations-style settings (team defaults, per-job override)
  integrations/     mail, calendar, Slack boundaries (+ gmail.py, graph.py, google_calendar.py adapters)
  board.py, demo.py text board / timeline rendering, end-to-end simulation
evals/
  dataset.py        labelled candidate replies + knowledge-base grounding cases
  run.py            scored evaluation runner (python -m evals.run)
```
