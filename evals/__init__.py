"""Offline evaluation harness for the reply agent.

Measures three things against a labelled dataset, with no network:

* classification accuracy of the analyze() step (per field);
* answer grounding — the compose() step must answer from the knowledge base or defer,
  and must never state a figure that isn't in a source;
* robustness to prompt-injection and sensitive-topic emails.

Run `python -m evals.run` for a report, or see tests/test_evals.py for the pass bar.
"""
