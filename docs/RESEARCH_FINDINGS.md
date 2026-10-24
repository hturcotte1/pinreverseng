# Pin / Autopilot: extended public-source research

*Second research pass (2026-10-24). Eight parallel researchers across independent public angles,
plus a completeness critic. Public sources only — same boundaries as `METHOD_AND_BOUNDARIES.md`.
Confidence is the researcher's, based on source quality; **[H]/[M]/[L]** = high/medium/low.*

This complements `TEARDOWN.md` (which covers the product's documented feature surface). Here the
focus is lineage, stack, the MCP object model, the published cadence data used to tune our
scheduler, and the contradictions between sources.

---

## 1. Founder lineage: Interseller → Pin

Steven Lu co-founded **Interseller** (~2016, with Jonathan Grana), a recruiting/sales outreach
platform acquired by **Greenhouse on 28 Oct 2021**; he later founded Pin. Interseller's documented
design reappears in Pin almost one-to-one, which makes it the best public evidence for how Pin's
outreach layer is built. **[H]** unless noted.

- Interseller's three pillars — **Source** (find & verify contact emails), **Outreach** (automated
  multi-step email sequences), **Connect** (sync to 30+ CRMs/ATSs) — map directly onto Pin.
  ([interseller.io/interseller-joins-greenhouse](https://www.interseller.io/interseller-joins-greenhouse))
- Email finding generated addresses from **name-pattern prediction**, ranked the most-likely
  address first, and verified it live with an **SMTP-style 7-check pipeline** (format, disposable,
  mail-server exists, server-recognizes-email, catch-all, spam-trap, Google-account), run both on
  sequence entry and again **before each send** to cut bounces.
  ([interseller.io/features/source/lookup-and-verify-emails](https://interseller.io/features/source/lookup-and-verify-emails))
- Lu personally published a 2019 analysis of email-address patterns across 5M+ companies
  **bucketed by headcount** (`{first}` dominates at tiny firms; `{f}{last}` mid-size; `{first}.{last}`
  at 1,000+), i.e. the headcount-aware prediction logic itself.
  ([interseller.io blog](https://www.interseller.io/blog/2019/02/04/top-email-address-patterns-by-company-size/))
- **"Deliverability by restraint":** sequences auto-stop on reply, round-robin scheduling, 3–4
  steps, ≤~300/day, ≥60s gaps, mailbox warm-up, OAuth to Gmail/Outlook/Exchange.
  ([help.interseller.io](https://help.interseller.io/article/231-getting-started-with-interseller))
- **Pin reproduces this nearly exactly [M]:** ≤200 emails/day per address, intervals ramped ~2min →
  5–10min, sequences capped at 3 emails, new domains warmed ~3 months, click-tracking discouraged.
  ([kb.pin.com deliverability](https://kb.pin.com/articles/2575439005-troubleshooting-email-deliverability-and-spam-issues))
- **Autopilot is the AI-era extension of Interseller's reply-detection:** Interseller merely stopped
  the sequence on reply; Autopilot *takes over* the conversation on reply.
  ([pin.com/blog/autopilot](https://www.pin.com/blog/autopilot/))

## 2. Tech stack (from the Trust Center + MCP docs)

All **[H]**, from Pin's own subprocessor list and MCP page.

- **Hosting:** AWS (all data + web infra); **Cloudflare** WAF/DNS/CDN.
- **Models:** multi-LLM — **Anthropic Claude, OpenAI, Google Gemini** — with **OpenRouter** for
  routing/HA and **Langfuse** for LLM observability.
- **Supporting SaaS:** Sentry (errors), PostHog (analytics), Loops (email), Retool (internal admin),
  Stripe (payments).
- **Integration aggregators:** **Kombo** (universal ATS — the real basis for "120+ ATS") and
  **Unipile** (messaging / LinkedIn / DMs).
  ([trust.pin.com/subprocessors](https://trust.pin.com/subprocessors))
- **Team:** founded 2024 by CEO Steven Lu; entity *Love Thy Recruiting, Inc.*; $3M Expa seed. The
  about page lists ~24 people incl. four named engineers; other sources say 12 or 8 — **headcount
  conflicts**. ([pin.com/about](https://www.pin.com/about))
- **Not disclosed anywhere public:** server languages/frameworks, the database engine, and the
  vector store behind the 850M-profile search. No public careers page.

## 3. MCP server & inferred object model

Pin ships a hosted recruiting MCP at `https://mcp.pin.com/mcp` — a stateless Streamable-HTTP
endpoint, **OAuth 2.1 + PKCE + dynamic client registration via WorkOS**, single `mcp` scope, no API
keys. It is the clearest public window into Pin's internal object model. **[H]** from
[pin.com/mcp](https://www.pin.com/mcp/) and [docs.pin.com/pin-mcp](https://docs.pin.com/pin-mcp.md).

**The 10 tools, in 4 phases:**

| Phase | Tools |
|---|---|
| Setup | `list_jobs`, `scrape_job_description` (Greenhouse/Lever/Ashby/any URL), `create_job` |
| Review | `get_candidates` (≤10 top-ranked **unreviewed**, with fit hints), `accept_candidate` (→ outreach), `reject_candidate` (+ reason, auto-backfills a replacement) |
| Tuning | `modify_search` (plain-language criteria, applied to next batch), `recalibrate_search` (feed accept/reject into the ranker, pull a fresh batch) |
| Memory | `read_job_memory`, `update_job_memory` (per-job, per-workspace slot: accepted signals, decline reasons, predicted criteria, confidence) |

- **Rate limits:** 60/min, 800/hr overall; `create_job` 2/min; candidate pulls 10/min + 100/hr; list
  views 15/min + 150/hr; **3,000 candidate views/org/day**, after which only IDs are returned.
- **Autonomous mode:** after the assistant **correctly predicts the recruiter's accept/reject 3+
  times in a row**, it offers to take over — fetch, decide, `recalibrate_search` / `update_job_memory`
  — stopping at the requested accept count or when confidence drops. The recruiter always opts in.
- **Inferred object model [M]:** a **Job** owns a **Search** whose criteria are mutated in place; the
  Search yields ranked, de-duplicated **Candidate** batches (≤10, status *unreviewed*) from the
  850M-profile index; each Candidate carries per-review state (accept/reject + reason) and a fit
  score; a backfill replenishes rejected slots; accepted candidates enter an **Outreach** sequence;
  each (job × workspace) has a **Memory** record. This is the same accept/reject→recalibrate learning
  loop our implementation models in `knowledge.py` + the Slack answer loop.
- **Surface evolution [H]:** `llms-full.txt` still documents a **prior 5-tool** local `npx` /stdio
  MCP (`search_candidates`, `get_candidate_profile`, `review_applicants`, `send_outreach`,
  `list_jobs`) with an inbound-applicant vs outbound-sourced distinction now abstracted away; the
  marketing demo shows a **third** illustrative tool set (`pin.draft_outreach`, `pin.pipeline_status`,
  `pin.add_to_shortlist`…). So docs lag the live surface, and a Shortlist/Pipeline-status surface
  likely exists beyond the canonical 10.

## 4. Published outreach cadence data (used to tune our scheduler)

From Pin's **2026 Recruiting Outreach Benchmark Report** (4M+ messages, 1,500+ orgs, Jun 2025–May
2026) and related studies. These are **vendor-reported** but large-sample, and are the empirical
basis for the defaults in `settings.py` / `scheduling.py`. **[H]** unless noted.

- **Reply timing:** median **~4 hours** (email 3.9h, LinkedIn 8.4h); **74.8% within 24h**, **93.9%
  within 7 days**; a separate study says ~90% within ~4 days.
  ([benchmark report](https://www.pin.com/blog/recruiting-outreach-benchmark-report/),
  [channel study](https://www.pin.com/blog/email-vs-linkedin-vs-sms-recruiting/))
- **Per-step reply share (email):** step 1 = 43.8%, step 2 = 33.7%, step 3 = 15.7%, step 4 = 4.5%,
  step 5+ = 2.3%. **3 touches capture 93.2%** of replies; **follow-ups earn 56.2%**. Recommended
  ceiling **3–4 touches**; stop before step 6.
- **Cadence spacing:** **~2 business days** between touches; **48h of silence → next touch**. Product
  examples: Day 1 email, Day 3 DM, Day 6 email, Day 9 SMS (gaps 2/3/3).
  ([automated-outreach](https://pin.com/features/automated-outreach))
- **Send time:** early local morning wins — **midnight–5am ET = 6.67%** (3–5am = 7.24%) vs midday
  4.81%. **Weekends weakest** (Sat 3.90%); Tue/Wed replies carry positive intent ~64% vs ~56–57% on
  weekends. "Land messages in the candidate's local morning."
- **Message length:** email peaks at **150–199 words / 800–1,200 chars** (+15% vs <400 chars);
  **LinkedIn is the opposite** — under 400 chars does best.
- **Channel reply rates:** automated email 4.96%, recruiter-written email 6.31%, **LinkedIn 17.08%
  (3.4×)**. SMS is manual-send only.
- **Conflict [L]:** two step-decay index curves disagree (100/98/76/60/53 vs 100/78/61/54/28).

> **How this tuned our code:** nudge after ~2 business days of silence (`nudge_after_days=3`,
> one calendar day of slack over the 48h rule), a single nudge by default (3 touches already capture
> ~93%), auto-archive after ~10 days (past the 7-day/93.9% reply mass), nudges sent in the
> candidate's local **weekday morning**, and a compose target of a concise-but-substantive email.
> See `settings.py` and `engine.py::_local_morning`.

## 5. Design thesis (from talks/podcasts)

- Lu's recurring thesis (Transform 2026, *"The Best Candidate is in Slot Number One"*): don't hand AI
  a human's Boolean tools faster — **rebuild the search engine** so the best-fit candidate surfaces
  first. Subjective judgment is resolved via natural-language filtering "in two questions or fewer."
  ([thepozcast.com](https://www.thepozcast.com/videos/the-best-candidate-is-in-slot-number-one-pins-ceo-steven-lu-on-rebuilding-recruiting-search-li/)) **[M]**
- **Calibration by design:** the ranker learns from accept/reject *without* explicit feedback (stated
  reasons accelerate it), and Pin deliberately surfaces an out-of-parameter candidate so a decline
  locates the decision boundary. **[M]**
- A **"shadow resume"** builds a profile from external data (GitHub, Stack Overflow, patents,
  publications) beyond the 850M index. ([Resilient Recruiter podcast](https://podcastaddict.com/the-resilient-recruiter/episode/222819980)) **[L]**

## 6. Model stack — contradiction resolved

A VentureBeat interview (Dec 2024) has Lu claiming Pin builds **in-house foundational models**, while
the current privacy policy lists **Gemini, OpenAI, Claude** as providers. The critic reconciled these:
VentureBeat's quotes scope the in-house models **specifically to resume reading and candidate
matching/ranking**, and never mention Google or claim in-house models run outreach/scheduling. **Most
likely a two-layer stack:** proprietary matching/ranking models + third-party LLMs for
generation/orchestration. No Pin page states which model does which — **inferred [M]**.
([VentureBeat](https://venturebeat.com/ai/pin-thinks-ai-can-fix-recruitments-biggest-problems-and-it-has-the-numbers-to-prove-it))

## 7. Autonomous scope & its contradictions

- Two modes, same capabilities, different latitude: **Assist** (approve each step) vs **Autopilot**
  (runs the desk). In Autopilot the agent sources 24/7, re-sources as profiles change, scores with a
  one-line auditable reason, sends email, follows up, books interviews. **Close & offer stays human
  in both modes.** ([ai-recruiting-agent](https://pin.com/features/ai-recruiting-agent/)) **[H]**
- **Unresolved multi-channel contradiction:** Pin's blogs say **LinkedIn/SMS are recruiter-sent
  tasks** (only email auto-sends); the feature-page task table says "Send outreach = Agent" without
  distinguishing channel; a third party (HeroHunt) claims Pin sends all three autonomously. Our
  implementation takes the conservative reading — **email auto-sends; LinkedIn/SMS would be queued
  tasks** — matching the independent SitePoint review.
  ([SitePoint](https://www.sitepoint.com/ai-recruiting-tools-for-agencies/))

## 8. Reviews, legal, and what stays unknown

- **Thin independent signal:** ~27 G2 (4.8/5), 2 Capterra, 7 Trustpilot (all 5★), 8 Chrome-store.
  **No Reddit or independent LinkedIn user posts surfaced.** Nearly every behavioral description
  traces back to Pin's marketing. One named Trustpilot user reports 4 calls booked within a day of
  ~45 min setup. ([Trustpilot](https://www.trustpilot.com/review/pin.com)) **[M]**
- **Reported weaknesses:** contact-data bounces, per-phone-number credit cost, credit caps that can
  exhaust mid-month, thin agency ATS connectors, not an ATS / no AI interviewer.
  ([thecognitive.io](https://thecognitive.io/blog/pin-alternatives)) **[M]**
- **Compliance gap [H]:** Pin asserts EEOC / NYC Local Law 144-defensible design but **publishes no
  bias audit and names no independent auditor**, which softens the "third-party fairness audits"
  marketing claim. LL144 requires the audit to be posted publicly.
  ([how-to-use-ai-in-hiring](https://www.pin.com/blog/how-to-use-ai-in-hiring/))
- **LinkedIn-removal event (Sep 2026)** rests entirely on two Pin-authored posts; no independent or
  trade-press corroboration, LinkedIn never quoted. **[L]**
- **No patents** attributable to Pin / Love Thy Recruiting / Lu (Google Patents returns zero;
  expected given 2024 founding + the ~18-month publication window — not proof none were filed). Lu's
  Interseller shows no patents either. **[H]**
- **Still unknown without authenticated access** (correctly out of scope): server languages, the
  database and vector store, exact prompts/models per step, the real step-decay curve, and whether
  LinkedIn/SMS can ever send autonomously.
