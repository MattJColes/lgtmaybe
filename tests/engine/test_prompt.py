"""Tests for prompt.py — system prompt builder."""

from __future__ import annotations

import pytest

from lgtmaybe.core.models import CustomLens, ReviewCategory, ReviewFinding, Severity
from lgtmaybe.engine.prompt import build_lens_prompt, build_system_prompt

# A term that appears only in each category's own section, used to prove a
# focused prompt carries its section and excludes the others'.
_SIGNATURE = {
    ReviewCategory.security: "owasp",
    ReviewCategory.correctness: "off-by-one",
    ReviewCategory.deprecation: "end-of-life",
    ReviewCategory.tests: "accompanying test",
    ReviewCategory.documentation: "docstring",
    ReviewCategory.performance: "n+1",
    ReviewCategory.complexity: "cyclomatic",
    ReviewCategory.intent: "stated intent",
}


def test_build_system_prompt_is_cached() -> None:
    """The per-category prompts are deterministic, so building one twice must
    return the identical cached object (the engine rebuilds them every batch)."""
    assert build_system_prompt(ReviewCategory.security) is build_system_prompt(
        ReviewCategory.security
    )
    assert build_system_prompt() is build_system_prompt()


def test_prompt_contains_all_severity_levels() -> None:
    prompt = build_system_prompt()
    for level in ("info", "low", "medium", "high", "critical"):
        assert level in prompt, f"severity level '{level}' missing from system prompt"


def test_prompt_contains_json_contract() -> None:
    prompt = build_system_prompt()
    # Must describe the JSON output fields
    for field in ("severity", "path", "line", "title", "body", "suggestion"):
        assert field in prompt, f"JSON field '{field}' missing from system prompt"


def test_prompt_asks_for_findings_envelope() -> None:
    """Structured output expects {"findings": [...]}, not a bare array."""
    prompt = build_system_prompt()
    assert "findings" in prompt
    assert '"findings"' in prompt
    assert '{"findings": []}' in prompt  # the empty-review shape


def test_prompt_instructs_changed_lines_only() -> None:
    prompt = build_system_prompt()
    # Must instruct model to comment only on changed lines
    assert "changed" in prompt.lower()


def test_prompt_is_nonempty_string() -> None:
    prompt = build_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 200


# ---------------------------------------------------------------------------
# Security-review coverage (the reviewer should actually hunt for vulns)
# ---------------------------------------------------------------------------


def test_prompt_directs_a_security_review() -> None:
    prompt = build_system_prompt().lower()
    assert "security" in prompt
    assert "owasp" in prompt


def test_prompt_names_common_vulnerability_classes() -> None:
    """The prompt should cue the model on the major OWASP-style vuln classes."""
    prompt = build_system_prompt().lower()
    expected = [
        "injection",
        "xss",  # cross-site scripting
        "secret",
        "auth",  # authn/authz
        "traversal",
        "ssrf",
        "deserialization",
        "crypto",
    ]
    missing = [term for term in expected if term not in prompt]
    assert not missing, f"security cues missing from prompt: {missing}"


def test_prompt_reaffirms_diff_is_untrusted_data() -> None:
    """Defence-in-depth: the system prompt itself restates the injection guard."""
    prompt = build_system_prompt().lower()
    assert "data" in prompt and ("untrusted" in prompt or "never follow" in prompt)


# ---------------------------------------------------------------------------
# Custom ("BYO") lenses — user-defined skill files run alongside the built-ins
# ---------------------------------------------------------------------------


def test_build_lens_prompt_carries_instructions_and_heading() -> None:
    lens = CustomLens(
        id="simplify",
        title="Simplify or delete",
        instructions="Flag code that should not exist at all — YAGNI.",
    )
    prompt = build_lens_prompt(lens)
    assert "Simplify or delete" in prompt  # heading uses the title
    assert "YAGNI" in prompt  # the user's instructions
    # Same scaffold as a built-in: severity rubric, JSON contract, shared rules.
    assert "Severity rubric" in prompt
    assert '{"findings": []}' in prompt
    assert "untrusted data" in prompt


def test_build_lens_prompt_falls_back_to_id_heading() -> None:
    lens = CustomLens(id="house-style", instructions="Enforce house style.")
    assert "## house-style" in build_lens_prompt(lens)


def test_build_lens_prompt_renders_supplied_example() -> None:
    finding = ReviewFinding(
        path="x.py", line=5, severity=Severity.low, title="needless wrapper", body="delete it"
    )
    lens = CustomLens(
        id="simplify",
        instructions="Flag needless code.",
        example_diff="--- a/x.py\n+++ b/x.py\n@@ -4,1 +4,2 @@\n def f():\n+    return g()\n",
        example_finding=finding,
    )
    prompt = build_lens_prompt(lens)
    assert "## Example" in prompt
    assert "needless wrapper" in prompt


def test_prompt_asks_for_deprecated_and_eol_review() -> None:
    """The reviewer should flag deprecated APIs and end-of-life dependencies."""
    prompt = build_system_prompt().lower()
    assert "deprecat" in prompt  # deprecated / deprecation
    assert "end-of-life" in prompt or "end of life" in prompt
    assert "dependenc" in prompt  # dependency / dependencies


def test_prompt_asks_for_logic_and_edge_case_review() -> None:
    """The reviewer should hunt correctness/logic bugs, not just security."""
    prompt = build_system_prompt().lower()
    assert "correctness" in prompt
    assert "off-by-one" in prompt
    assert "boundary" in prompt
    assert "dereference" in prompt  # null/None dereferences


def test_prompt_asks_for_test_coverage() -> None:
    """Changed code paths shipped without a test should be flagged."""
    prompt = build_system_prompt().lower()
    assert "coverage" in prompt
    assert "accompanying test" in prompt
    assert "suggestion" in prompt  # a runnable test goes in the suggestion field


def test_prompt_asks_for_documentation_review() -> None:
    """Public surfaces added without docs should be flagged, restrained to public APIs."""
    prompt = build_system_prompt().lower()
    assert "documentation" in prompt
    assert "docstring" in prompt
    assert "public" in prompt


def test_prompt_names_pii_and_secrets_in_logs() -> None:
    """Sensitive-data exposure should name concrete PII/secret leaks into logs."""
    prompt = build_system_prompt().lower()
    assert "log" in prompt
    assert "pii" in prompt
    assert "ssn" in prompt  # SSNs
    assert "password" in prompt


def test_prompt_asks_for_performance_review() -> None:
    """The reviewer should flag performance regressions, graded by impact."""
    prompt = build_system_prompt().lower()
    assert "performance" in prompt
    assert "n+1" in prompt  # N+1 queries / repeated calls in a loop
    assert "quadratic" in prompt


def test_prompt_asks_for_complexity_review() -> None:
    """The reviewer should flag needless complexity, restrained and low severity."""
    prompt = build_system_prompt().lower()
    assert "complexity" in prompt
    assert "cyclomatic" in prompt
    assert "nest" in prompt  # deep nesting
    assert "duplicat" in prompt  # duplicated logic to extract


# ---------------------------------------------------------------------------
# Per-category fan-out: each lens gets its own focused prompt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", list(ReviewCategory), ids=lambda c: c.value)
def test_focused_prompt_carries_its_section_and_the_shared_contract(
    category: ReviewCategory,
) -> None:
    prompt = build_system_prompt(category).lower()
    # Its own section is present...
    assert _SIGNATURE[category] in prompt
    # ...and the shared output contract travels with every category.
    for field in ("severity", "path", "line", "title", "body", "suggestion"):
        assert field in prompt


@pytest.mark.parametrize("category", list(ReviewCategory), ids=lambda c: c.value)
def test_focused_prompt_excludes_other_categories(category: ReviewCategory) -> None:
    prompt = build_system_prompt(category).lower()
    for other, marker in _SIGNATURE.items():
        if other is not category:
            assert marker not in prompt, f"{category.value} prompt leaked {other.value} section"


def test_full_prompt_still_contains_every_category() -> None:
    """The no-arg call is the union of all sections (backward compatible)."""
    prompt = build_system_prompt().lower()
    for marker in _SIGNATURE.values():
        assert marker in prompt


# ---------------------------------------------------------------------------
# Topic coverage: concurrency, numeric/time bugs, CI/IaC, weak tests, stale docs
# ---------------------------------------------------------------------------


def test_prompt_asks_for_concurrency_and_race_review() -> None:
    """Races, TOCTOU, and async mistakes are first-class correctness targets."""
    prompt = build_system_prompt(ReviewCategory.correctness).lower()
    assert "race" in prompt
    assert "toctou" in prompt
    assert "await" in prompt  # coroutine called without await / blocking in async


def test_prompt_asks_for_numeric_and_datetime_review() -> None:
    """Numeric and date/time bug classes are cued explicitly."""
    prompt = build_system_prompt(ReviewCategory.correctness).lower()
    assert "timezone" in prompt
    assert "division by zero" in prompt
    assert "float" in prompt
    assert "mutable default" in prompt


def test_prompt_names_csrf_redirect_xxe_and_mass_assignment() -> None:
    prompt = build_system_prompt(ReviewCategory.security).lower()
    assert "csrf" in prompt
    assert "redirect" in prompt
    assert "xxe" in prompt
    assert "mass assignment" in prompt
    assert "redos" in prompt or "backtracking" in prompt


def test_prompt_covers_ci_and_iac_misconfiguration() -> None:
    """Workflow/IaC files are a review surface, not just application code."""
    prompt = build_system_prompt(ReviewCategory.security).lower()
    assert "workflow" in prompt
    assert "iam" in prompt
    assert "container" in prompt
    assert "pinned" in prompt or "sha" in prompt  # unpinned third-party actions


def test_prompt_flags_weak_tests_not_just_missing_ones() -> None:
    prompt = build_system_prompt(ReviewCategory.tests).lower()
    assert "assertion-free" in prompt or "no assertions" in prompt
    assert "mock" in prompt
    assert "sleep" in prompt


def test_prompt_flags_stale_documentation() -> None:
    """A docstring/comment the diff just made wrong is worse than no docs."""
    prompt = build_system_prompt(ReviewCategory.documentation).lower()
    assert "stale" in prompt
    assert "contradict" in prompt


def test_prompt_flags_unbounded_growth_and_leaks() -> None:
    prompt = build_system_prompt(ReviewCategory.performance).lower()
    assert "cache" in prompt
    assert "eviction" in prompt


def test_prompt_flags_typosquats_and_license_conflicts() -> None:
    prompt = build_system_prompt(ReviewCategory.deprecation).lower()
    assert "typosquat" in prompt
    assert "license" in prompt


# ---------------------------------------------------------------------------
# Intent lens: does the change do what the PR says it does?
# ---------------------------------------------------------------------------


def test_prompt_asks_for_intent_review() -> None:
    prompt = build_system_prompt(ReviewCategory.intent).lower()
    assert "stated intent" in prompt
    assert "out-of-scope" in prompt or "out of scope" in prompt
    assert "commit" in prompt  # commit messages carry the intent on the CLI


def test_intent_prompt_treats_intent_text_as_data() -> None:
    """Intent text is attacker-controlled; the lens must not obey it."""
    prompt = build_system_prompt(ReviewCategory.intent).lower()
    assert "untrusted" in prompt or "not" in prompt and "instructions" in prompt


# ---------------------------------------------------------------------------
# Prompt mechanics: worked example per lens, line-number mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", list(ReviewCategory), ids=lambda c: c.value)
def test_each_focused_prompt_carries_a_worked_example(category: ReviewCategory) -> None:
    """Every lens gets a category-appropriate few-shot example (a security-flavoured
    example on a docs/tests lens anchors the model to the wrong finding type)."""
    prompt = build_system_prompt(category)
    assert prompt.count("## Example") == 1
    assert "@@ -" in prompt  # the example diff shows a real hunk header


def test_prompt_explains_line_number_mapping() -> None:
    """`line` is a file line number computed from the hunk header — a wrong line
    means the finding silently maps to nothing and is dropped."""
    prompt = build_system_prompt()
    assert "hunk header" in prompt.lower()
    assert "LEFT" in prompt and "RIGHT" in prompt


def test_prompt_asks_for_one_finding_per_distinct_issue() -> None:
    prompt = build_system_prompt().lower()
    assert "each distinct issue" in prompt
