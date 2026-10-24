# pinreverseng

A public-source teardown of **Pin's Autopilot** (an AI recruiting reply agent launched
2026-10-23), plus a **clean-room reimplementation** of the same behaviour.

* [`docs/TEARDOWN.md`](docs/TEARDOWN.md): what Autopilot is, its complete documented feature
  surface, how it grew out of earlier Pin releases, the vendor stack, and an inferred
  architecture with confidence levels. Every claim is sourced.
* [`docs/BLACKBOX_TEST_PLAN.md`](docs/BLACKBOX_TEST_PLAN.md): 24 experiments to settle the open
  questions by using the product normally on your own account.
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): how this implementation works.

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
python -m pytest                  # 63 tests
python -m autopilot.demo          # simulated two weeks: 6 candidates, Slack Q&A, learning, booking, nudges
python -m autopilot.demo --drafts # same, in review-every-draft mode
python -m autopilot.demo --claude # needs Anthropic credentials (ANTHROPIC_API_KEY or `ant auth login`)
```

The Claude path is unit-tested against a fake client: request shape, schema parsing, refusal
fallback. It has not yet been exercised against the live API.

## Layout
```
src/autopilot/
  engine.py         orchestration: inbound -> analyze -> policy -> compose -> verify -> deliver; timers
  brain/            analyze / compose / distil_fact interface; heuristic + Claude implementations; prompts
  knowledge.py      company/job/learned facts with ids
  scheduling.py     slot finding, labels, US holidays
  guardrails.py     citation verification, text checks, auto-send eligibility
  pipeline.py       stage transition rules
  settings.py       Outreach-Automations-style settings (team defaults, per-job override)
  integrations/     mail, calendar, Slack boundaries (+ in-memory versions)
  board.py, demo.py text board / timeline rendering, end-to-end simulation
```
