# Visual dashboard demo

An original recruiting-dashboard UI that renders the **real output** of the agent — not a mockup.
`generate.py` drives a scenario (7 candidates) through the actual engine and writes the resulting
state into `index.html`; `shot.js` screenshots it with the pre-installed Playwright/Chromium.

```bash
pip install -e '.[dev]'                 # from the repo root
python demo/dashboard/generate.py       # runs the scenario -> demo/dashboard/index.html
node   demo/dashboard/shot.js           # -> board.png, inbox.png, analytics.png, dashboard_full.png
# or just open demo/dashboard/index.html in a browser
```

Everything shown — pipeline stages, "Sent by agent" labels, the recruiter's calibrated voice,
candidate signals, flight plans, the conversion funnel with leak reasons, the autonomy governor,
and knowledge-base readiness — is produced by `src/autopilot`. The data is a scripted demo, not a
real company. The UI is a generic, original design and not a copy of any product's interface.
