# How this agent goes beyond Pin's Autopilot

Pin's Autopilot is **reactive**: a candidate replies, it answers from a text-box knowledge base,
books, nudges, and archives. This implementation keeps all of that and adds the things a great
human recruiter does that Pin doesn't — reading the relationship, protecting the candidate's
experience, proving what it's about to do, and earning autonomy from evidence. Every capability
below has a deterministic, offline-tested core (the Claude brain is optional), and a safety rule.

The priorities were pressure-tested by a five-lens design panel (candidate experience, recruiter
trust/safety, conversion/analytics, writing quality, autonomy/scale) plus a completeness critic;
this is that panel's first build wave.

| Capability (module) | What Pin does | What this adds | Safety rule |
|---|---|---|---|
| **Objection + sentiment + at-risk** (`insights.py`) | Treats a reply as a question to answer or a "no" to archive | Reads a 9-code objection taxonomy, a warming/steady/cooling trend, and an at-risk flag from every reply | Hard money/competing concerns are never auto-answered |
| **Objection rescue** (`engine.py`) | Archives a "no" | One KB-grounded, low-pressure attempt to turn a *recoverable* soft "no" around | One attempt only; never argues a hard dealbreaker, unsubscribe, or escalation |
| **Groundedness + experience gate** (`guardrails.py`) | Blocks invented figures, offers, protected-characteristic questions | Also blocks prose over-promising ("you'll love it", "best team"), pressure, unauthorized commitments, and dropped questions | The gate can only downgrade a message to a human-reviewed draft — never send |
| **Self-maintaining knowledge base** (`coverage.py`) | A text box; gaps surface only mid-conversation | Pre-outreach coverage report + gap checklist, contradiction detection (two salary figures, visa both ways), and staleness TTL on learned facts | Defers rather than guessing; outreach-readiness gates on zero conflicts |
| **Per-recruiter voice** (`voice.py`) | Calibrates outreach on five sample messages | Deterministic StyleProfile (greeting, sign-off, warmth, formality, length) applied in both brains | Governs surface form only; fact/figure guardrails stay authoritative around it |
| **Interview concierge** (`engine.py`) | Books the interview and stops | Day-before prep (grounded in the process fact) and a day-of confirmation, each re-checking the live meeting at fire time | Confirmation is a budget-exempt high-priority lane; reminders reset on reschedule |
| **Shared touch budget** (`engine.py`) | Fixed nudge/archive timers | One budget + cooldown across *all* agent-initiated sends (nudge, re-engage, lifecycle) | Unsubscribe / not-interested / needs-human are hard global exemptions |
| **Conversion ledger** (`ledger.py`) | Vanity agent stats | Real funnel (replied → … → hired) with step conversion, dwell times, and a named leak reason + stage per lost candidate | Reports sample size; makes no recommendations below it |
| **Flight plan** (`flightplan.py`) | No per-message rationale | An inspectable record per outbound: cited facts, reversible vs irreversible effects, auto-send vs draft and why | — |
| **Shadow mode** (`shadow.py`) | — | Runs the full pipeline on live inbound, delivers nothing, scores agreement vs the recruiter's real action | Never delivers; run on a throwaway store |
| **Autonomy governor** (`governor.py`) | A global drafts/auto toggle | A per-job trust tier (autonomous / assisted / draft-only) earned and revoked from telemetry, with a sticky circuit breaker | Never silences a message — the floor is always a human-reviewed draft |

## The through-line: autonomy you can trust at scale

Pin's own weak spots from the research (`RESEARCH_FINDINGS.md`) are exactly what this targets:
self-contradiction on what it will send, no published bias audit, a flat knowledge box, and a
purely reactive loop. The design here is **model classifies and writes; deterministic code
decides**, so:

- a prompt-injected or manipulative email can at worst produce a bad *classification*, never an
  un-enumerated action (proven by the injection evals);
- every factual sentence is cited to a knowledge-base fact or deferred (grounding evals at 100%);
- every proactive behavior shares one touch budget, so the new autonomy can't become pestering;
- nothing the agent does is silent — there's a flight plan for every message, a leak reason for
  every lost candidate, and a governor that drops to human-reviewed drafts the moment the numbers
  turn, staying there until a person re-arms it.

## What still needs live validation

This is a reference implementation with a strong offline test suite (126 tests) and a 100% eval
score on the heuristic brain. Not yet done, and honestly flagged:

- the Claude brain path is tested against a fake client, not the live API (no key in this env);
- shadow-agreement, conversion, and governor thresholds are reasonable defaults, not tuned on real
  traffic — they need a real dataset (see `BLACKBOX_TEST_PLAN.md` and the eval harness);
- the panel's second wave (adaptive send-time, booking optimizer, multi-channel with TCPA/ToS
  consent rules, a demographic-firewall adverse-impact sentinel, A/B experimentation, a crash-safe
  scale substrate) is designed but not built — see the synthesis in the design workflow output.
