# Method and boundaries

This project is **competitive analysis plus a clean-room rebuild**. It is deliberately scoped to
stay inside what is lawful and ethical for that kind of work.

## What was used
* Pin's public marketing site and feature pages.
* Pin's public help center (`docs.pin.com`), which Pin serves as Markdown via `docs.pin.com/llms.txt`
  and whose `robots.txt` explicitly welcomes AI crawlers.
* Public blog and product-update posts, the public MCP documentation, the public trust-center
  subprocessor list, press coverage, patents, job postings, podcasts, and third-party reviews.

Every factual claim in `TEARDOWN.md` and `RESEARCH_FINDINGS.md` carries a source and a confidence
level.

## What was NOT done
These were ruled out from the start and never crossed:
* No logging into Pin or using anyone's account.
* No decompiling, deobfuscating, or inspecting Pin's application code or JavaScript bundles.
* No calling, probing, fuzzing, or reverse-engineering Pin's private APIs.
* No scraping of the authenticated product.
* No acquisition or use of Pin's source code, prompts, model weights, or other trade secrets.

The server-side logic that makes Autopilot work — the exact prompts, model choices, thresholds,
and learning rules — is not in anything Pin ships to a browser, so there is no way to obtain it
without unauthorized access. That is out of scope by design. Where the implementation needed a
value Pin doesn't publish, it uses an independent, clearly-labelled choice (see `TEARDOWN.md` §6),
not anything taken from Pin.

## The clean-room rebuild
`src/autopilot` is written from the *described behaviour* of the product, the way a competitor or
an interoperating vendor would build their own version. It contains no Pin code or content and is
not affiliated with or endorsed by Pin.

## Before relying on any of this
If you intend to benchmark Pin from your own account (see `BLACKBOX_TEST_PLAN.md`), confirm with
whoever owns legal questions at your company that your plan is consistent with Pin's Terms of
Service. Normal product evaluation on your own paid account is usually permitted; automated
scraping and competitive benchmarking sometimes are not. This document is not legal advice.
