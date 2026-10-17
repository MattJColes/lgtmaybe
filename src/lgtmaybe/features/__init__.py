"""lgtmaybe.features: what we post beside the review.

The review pipeline (``lgtmaybe.engine``) turns a pull request into findings.
These modules are its siblings: each makes its own model call or derives its
own output, and the pipeline needs none of them.

- ``describe``: the PR description (``/describe`` and the overview's first
  section), plus the one-call scaffold the other model-backed features share;
- ``diagram``: the change diagrams (``/diagram``);
- ``high_impact``: the overview's High Impact Areas section;
- ``overview``: the change overview comment that composes the three above with
  the risk-of-change section;
- ``labels``: the review-effort, security, splitting and risk labels;
- ``validate``: follow-up validation of earlier findings against a newer head.

Imports go one way. A feature may import the engine's public helpers
(redaction, injection wrappers, the parser, the risk verdict); no engine module
imports this package. ``tests/test_code_quality.py`` holds that line.
"""
