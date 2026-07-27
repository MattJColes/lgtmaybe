"""Tests for prompt.py — system prompt builder."""

from __future__ import annotations

import pytest

from lgtmaybe.core.models import CustomLens, ReviewCategory, ReviewFinding, Severity
from lgtmaybe.engine.prompt import build_lens_prompt, build_shared_preamble, build_system_prompt
from lgtmaybe.engine.redact import REDACTED_PLACEHOLDER


def _union_prompt() -> str:
    """The union of every focused lens prompt — a convenient assertion surface.

    Production only ever builds per-category prompts; asserting against the
    union checks "some lens covers X" without caring which one.
    """
    return "\n\n".join(build_system_prompt(c) for c in ReviewCategory)


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
    ReviewCategory.ponytail: "yagni",
}


def test_build_system_prompt_is_cached() -> None:
    """The per-category prompts are deterministic, so building one twice must
    return the identical cached object (the engine rebuilds them every batch)."""
    assert build_system_prompt(ReviewCategory.security) is build_system_prompt(
        ReviewCategory.security
    )


def test_prompt_contains_all_severity_levels() -> None:
    prompt = _union_prompt()
    for level in ("info", "low", "medium", "high", "critical"):
        assert level in prompt, f"severity level '{level}' missing from system prompt"


def test_prompt_contains_json_contract() -> None:
    prompt = _union_prompt()
    # Must describe the JSON output fields
    for field in (
        "severity",
        "path",
        "line",
        "title",
        "body",
        "failure_scenario",
        "suggestion",
    ):
        assert field in prompt, f"JSON field '{field}' missing from system prompt"


@pytest.mark.parametrize(
    "category",
    [
        ReviewCategory.security,
        ReviewCategory.correctness,
        ReviewCategory.deprecation,
        ReviewCategory.performance,
    ],
)
def test_defect_examples_include_a_failure_scenario(category: ReviewCategory) -> None:
    prompt = build_system_prompt(category)

    assert '"failure_scenario": "' in prompt


@pytest.mark.parametrize(
    "category",
    [
        ReviewCategory.tests,
        ReviewCategory.documentation,
        ReviewCategory.complexity,
        ReviewCategory.intent,
        ReviewCategory.ponytail,
    ],
)
def test_gap_examples_use_a_null_failure_scenario(category: ReviewCategory) -> None:
    prompt = build_system_prompt(category)

    assert '"failure_scenario": null' in prompt


def test_prompt_requires_failure_scenarios_independently_of_severity() -> None:
    prompt = _union_prompt().lower()

    assert "trigger" in prompt
    assert "observable impact" in prompt
    assert "regardless of severity" in prompt


def test_contract_requires_suggestion_to_be_replacement_code() -> None:
    """`suggestion` is rendered in a committable code fence, so it must be literal
    replacement code (or null) — never prose. Explanation belongs in `body`."""
    prompt = _union_prompt().lower()
    assert "suggestion" in prompt
    # The contract must tie the field to committable replacement code...
    assert "replacement code" in prompt or "replacement source code" in prompt
    # ...and explicitly steer prose into the body, not the suggestion.
    assert "prose" in prompt


def test_contract_binds_suggestion_names_to_the_target_file() -> None:
    """A suggestion is committed verbatim, so its names must resolve in the file
    being changed — not in whichever worked example this prompt happened to show.

    The failure this guards against is real and example-seeded: the deprecation
    example fixes `datetime.utcnow()` in a file that does `import datetime`, so it
    suggests `datetime.timezone.utc`. Shown a file that does
    `from datetime import datetime`, a model carried that qualifier across and
    produced `datetime.now(tz=datetime.timezone.utc)` — an AttributeError, since
    there `datetime` is the class, not the module. Committing that suggestion
    replaces working code with a crash.
    """
    # Asserted on BOTH prompt shapes: prompt_cache defaults on, so the shared
    # preamble is the one production actually sends. A rule that lived only in
    # the legacy system prompt would be absent from every default review.
    for prompt in (_union_prompt().lower(), build_shared_preamble().lower()):
        # The contract must scope name resolution to the file under review...
        assert "imports" in prompt
        # ...and say what to do when the fix needs a name the file lacks.
        assert "import" in prompt and "body" in prompt


def test_worked_example_suggestions_are_code_not_prose() -> None:
    """Every non-null worked-example suggestion must read as code, not an English
    instruction — the model copies these verbatim."""
    prose_starts = ("use ", "consider ", "prefer ", "avoid ", "you should ", "add ")
    for category in ReviewCategory:
        prompt = build_system_prompt(category)
        for line in prompt.splitlines():
            stripped = line.strip()
            if not stripped.startswith('"suggestion":'):
                continue
            value = stripped.split(":", 1)[1].strip().rstrip(",")
            if value == "null":
                continue
            lowered = value.strip('"').lower()
            assert not lowered.startswith(prose_starts), (
                f"{category} example suggestion looks like prose: {value}"
            )


def test_prompt_asks_for_findings_envelope() -> None:
    """Structured output expects {"findings": [...]}, not a bare array."""
    prompt = _union_prompt()
    assert "findings" in prompt
    assert '"findings"' in prompt
    assert '{"findings": []}' in prompt  # the empty-review shape


def test_prompt_instructs_changed_lines_only() -> None:
    prompt = _union_prompt()
    # Must instruct model to comment only on changed lines
    assert "changed" in prompt.lower()


def test_prompt_is_nonempty_string() -> None:
    prompt = _union_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 200


def test_prompt_forbids_narration_only_findings() -> None:
    # The weak-model failure mode is INFO-level narration that just restates the
    # diff ("X was removed"). The contract must tell the model that's not a finding.
    prompt = _union_prompt().lower()
    assert "narrat" in prompt or "describe" in prompt
    assert "restate" in prompt or "merely" in prompt


# ---------------------------------------------------------------------------
# Security-review coverage (the reviewer should actually hunt for vulns)
# ---------------------------------------------------------------------------


def test_prompt_directs_a_security_review() -> None:
    prompt = _union_prompt().lower()
    assert "security" in prompt
    assert "owasp" in prompt


def test_prompt_names_common_vulnerability_classes() -> None:
    """The prompt should cue the model on the major OWASP-style vuln classes."""
    prompt = _union_prompt().lower()
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
    prompt = _union_prompt().lower()
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
    prompt = _union_prompt().lower()
    assert "deprecat" in prompt  # deprecated / deprecation
    assert "end-of-life" in prompt or "end of life" in prompt
    assert "dependenc" in prompt  # dependency / dependencies


def test_prompt_asks_for_logic_and_edge_case_review() -> None:
    """The reviewer should hunt correctness/logic bugs, not just security."""
    prompt = _union_prompt().lower()
    assert "correctness" in prompt
    assert "off-by-one" in prompt
    assert "boundary" in prompt
    assert "dereference" in prompt  # null/None dereferences


def test_prompt_asks_for_test_coverage() -> None:
    """Changed code paths shipped without a test should be flagged."""
    prompt = _union_prompt().lower()
    assert "coverage" in prompt
    assert "accompanying test" in prompt
    assert "suggestion" in prompt  # a runnable test goes in the suggestion field


def test_prompt_asks_for_documentation_review() -> None:
    """Public surfaces added without docs should be flagged, restrained to public APIs."""
    prompt = _union_prompt().lower()
    assert "documentation" in prompt
    assert "docstring" in prompt
    assert "public" in prompt


def test_prompt_names_pii_and_secrets_in_logs() -> None:
    """Sensitive-data exposure should name concrete PII/secret leaks into logs."""
    prompt = _union_prompt().lower()
    assert "log" in prompt
    assert "pii" in prompt
    assert "ssn" in prompt  # SSNs
    assert "password" in prompt


def test_prompt_asks_for_performance_review() -> None:
    """The reviewer should flag performance regressions, graded by impact."""
    prompt = _union_prompt().lower()
    assert "performance" in prompt
    assert "n+1" in prompt  # N+1 queries / repeated calls in a loop
    assert "quadratic" in prompt


def test_prompt_asks_for_complexity_review() -> None:
    """The reviewer should flag needless complexity, restrained and low severity."""
    prompt = _union_prompt().lower()
    assert "complexity" in prompt
    assert "cyclomatic" in prompt
    assert "nest" in prompt  # deep nesting
    assert "duplicat" in prompt  # duplicated logic to extract


def test_prompt_asks_for_ponytail_review() -> None:
    """The 'lazy senior dev' lens: flag code that needn't exist (YAGNI, use stdlib)."""
    prompt = _union_prompt().lower()
    assert "yagni" in prompt
    assert "never wrote" in prompt  # the best code is the code you never wrote
    assert "standard library" in prompt


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


def test_union_of_focused_prompts_contains_every_category() -> None:
    """Every category's signature section appears in some focused prompt."""
    prompt = _union_prompt().lower()
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
    prompt = _union_prompt()
    assert "hunk header" in prompt.lower()
    assert "LEFT" in prompt and "RIGHT" in prompt


def test_prompt_asks_for_one_finding_per_distinct_issue() -> None:
    prompt = _union_prompt().lower()
    assert "each distinct issue" in prompt
    # The pipeline dedupes to one finding per (path, line, side), so the prompt
    # must not solicit several findings sharing a line — output it would discard.
    assert "share a line" not in prompt


def test_prompt_asks_for_a_verbatim_anchor() -> None:
    prompt = _union_prompt()
    assert "anchor" in prompt
    assert "verbatim" in prompt.lower()


def test_every_category_example_includes_an_anchor() -> None:
    for category in ReviewCategory:
        prompt = build_system_prompt(category)
        assert '"anchor"' in prompt, f"{category} example missing an anchor field"


def test_prompt_warns_minus_lines_are_removed_not_duplicates() -> None:
    prompt = _union_prompt().lower()
    assert "removed" in prompt
    # A modify pair must not be read as a redefinition / duplication.
    assert "defined twice" in prompt or "duplicat" in prompt


def test_prompt_demands_codebase_humility_about_unseen_code() -> None:
    """The diff is only a slice of the codebase. A finding that asserts a
    guard/field/handler is "missing" may be wrong because that thing lives in an
    unshown file — so every lens must be told to hedge such claims, not assert
    them. The rule lives in shared rules, so it appears in every focused prompt."""
    for category in ReviewCategory:
        prompt = build_system_prompt(category).lower()
        assert "cannot see" in prompt or "not shown" in prompt
        assert "missing" in prompt
        # tells the model to hedge + lower severity rather than assert absence
        assert "hedge" in prompt
        assert "severity" in prompt


def test_humility_rule_present_in_custom_lens_prompt() -> None:
    lens = CustomLens(id="x", instructions="flag foo")
    assert "cannot see" in build_lens_prompt(lens).lower()


def test_prompt_warns_cross_hunk_is_one_file_not_duplicate() -> None:
    for category in ReviewCategory:
        prompt = build_system_prompt(category).lower()
        assert "windows into the same file" in prompt
        assert "defined twice" in prompt


def test_prompt_guards_whole_file_symbol_claims() -> None:
    for category in ReviewCategory:
        prompt = build_system_prompt(category).lower()
        assert "undefined" in prompt
        assert "unless the diff" in prompt
        assert "hedge" in prompt


def test_prompt_guards_unused_import_inside_functions() -> None:
    for category in ReviewCategory:
        prompt = build_system_prompt(category).lower()
        assert "unused" in prompt
        assert "depends(" in prompt


def test_prompt_demands_library_and_cloud_semantics_humility() -> None:
    for category in ReviewCategory:
        prompt = build_system_prompt(category).lower()
        assert "sdk" in prompt
        assert "encoding" in prompt
        assert "iam" in prompt or "access policy" in prompt
        assert "index" in prompt


def test_prompt_does_not_predict_test_runtime_failures() -> None:
    prompt = build_system_prompt(ReviewCategory.tests).lower()
    assert "cannot run the suite" in prompt
    assert "fixtures" in prompt
    assert "predict" in prompt


def test_prompt_intent_fulfilment_is_not_a_defect() -> None:
    prompt = build_system_prompt(ReviewCategory.intent).lower()
    assert "fulfils the stated intent" in prompt or "fulfils the intent" in prompt
    assert "deliberate removal" in prompt


# ---------------------------------------------------------------------------
# Token hygiene — shared instructions are stated once, not per lens/example
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", list(ReviewCategory))
def test_empty_review_shape_stated_once(category: ReviewCategory) -> None:
    """`{"findings": []}` is stated once (in the shared rules), not repeated in
    every worked example — the examples ride the uncached per-lens block, so a
    per-example restatement is paid on every fan-out call."""
    assert build_system_prompt(category).count('{"findings": []}') == 1


@pytest.mark.parametrize("category", list(ReviewCategory))
def test_lens_sections_do_not_restate_the_changed_lines_rule(category: ReviewCategory) -> None:
    """The changed-lines-only rule lives in the shared rules (cached preamble);
    per-lens restatements ride the uncached suffix on every call."""
    prompt = build_system_prompt(category)
    assert "only raise findings on changed lines" not in prompt
    # The authoritative shared-rules statement remains.
    assert "Comment ONLY on changed lines" in prompt


def test_json_only_rule_not_duplicated_in_shared_rules() -> None:
    """The output contract's 'Return ONLY a JSON object' is not restated as a
    second shared-rules bullet."""
    preamble = build_shared_preamble()
    assert "Return ONLY a JSON object" in preamble
    assert "Never output anything other than" not in preamble


_LANG = "Japanese"


def test_language_directive_absent_by_default() -> None:
    """Unset language ⇒ the preamble and every legacy per-category prompt are
    byte-identical to the pre-language build — the prompt-cache contract depends
    on this default staying stable."""
    default = build_shared_preamble()
    assert build_shared_preamble(None) == default
    assert "Output language" not in default
    assert "do not translate" not in default
    for category in ReviewCategory:
        assert build_system_prompt(category, None) == build_system_prompt(category)
        assert "Output language" not in build_system_prompt(category)


def test_language_directive_present_when_set() -> None:
    """A set language adds a directive naming the prose fields (`title`/`body`)
    and the language, in both the split preamble and the legacy prompt."""
    preamble = build_shared_preamble(_LANG)
    assert preamble != build_shared_preamble()
    assert _LANG in preamble
    assert "`title`" in preamble and "`body`" in preamble
    # Structural fields + suggestion code stay untranslated.
    assert "do not translate" in preamble
    legacy = build_system_prompt(ReviewCategory.security, _LANG)
    assert _LANG in legacy and "`title`" in legacy and "do not translate" in legacy


def test_custom_lens_prompt_carries_language_directive() -> None:
    lens = CustomLens(id="naming", instructions="Flag bad names.")
    assert build_lens_prompt(lens, None) == build_lens_prompt(lens)
    assert _LANG in build_lens_prompt(lens, _LANG)


# ---------------------------------------------------------------------------
# dependency health: the model stops guessing once a scanner covers it
# ---------------------------------------------------------------------------


def test_dependency_health_bullets_are_present_by_default() -> None:
    """Nothing changes for the vast majority — static analysis is opt-in."""
    prompt = build_system_prompt(ReviewCategory.deprecation).lower()

    assert "known" in prompt and "advisor" in prompt
    assert "abandoned" in prompt or "yanked" in prompt


def test_advisory_claims_drop_when_a_scanner_covers_them() -> None:
    """A knowledge cutoff cannot answer "is this version vulnerable?".

    With osv-scanner reporting advisories deterministically, asking the model
    for them too only invites a confident wrong answer beside an accurate one.
    """
    narrowed = build_system_prompt(ReviewCategory.deprecation, dependency_health=False).lower()

    assert "advisor" not in narrowed
    assert "abandoned" not in narrowed and "yanked" not in narrowed


def test_narrowing_keeps_what_a_scanner_cannot_answer() -> None:
    """Deprecated APIs, EOL runtimes and typosquats are not in a CVE database."""
    narrowed = build_system_prompt(ReviewCategory.deprecation, dependency_health=False).lower()

    assert "deprecat" in narrowed
    assert "end-of-life" in narrowed
    assert "typosquat" in narrowed
    assert "license" in narrowed


def test_the_merged_code_health_lens_narrows_too() -> None:
    """Under the default fast preset deprecation is not its own call."""
    from lgtmaybe.engine.prompt import CODE_HEALTH_GROUP, build_group_block

    full = build_group_block(CODE_HEALTH_GROUP).lower()
    narrowed = build_group_block(CODE_HEALTH_GROUP, dependency_health=False).lower()

    assert "known-vulnerable" in full
    assert "known-vulnerable" not in narrowed
    # The category attribution the fast preset depends on must survive.
    assert '"deprecation"' in build_group_block(CODE_HEALTH_GROUP, dependency_health=False)


# ---------------------------------------------------------------------------
# the redaction marker: lgtmaybe's own placeholder is not a finding
# ---------------------------------------------------------------------------


def test_shared_rules_name_the_redaction_marker() -> None:
    """`redact()` rewrites every secret it matches to `[REDACTED]` before the
    diff is sent, so that token is lgtmaybe's own marker, never source content.
    Nothing in the prompt said so, and the security lens actively invited a
    finding on it — a false positive manufactured by our own redaction."""
    preamble = build_shared_preamble()

    assert REDACTED_PLACEHOLDER in preamble


def test_no_lens_is_told_to_flag_redacted_placeholders() -> None:
    """The security lens used to say "even if they look redacted, flag the
    practice" — which turns every redacted diff into a finding."""
    for category in ReviewCategory:
        prompt = build_system_prompt(category).lower()
        assert "look redacted" not in prompt
        assert "even if they look" not in prompt


# ---------------------------------------------------------------------------
# secret scanning: the model stops guessing once gitleaks covers it
# ---------------------------------------------------------------------------


def test_hardcoded_secret_bullet_is_present_by_default() -> None:
    """Static analysis is opt-in, so the default prompt is unchanged."""
    prompt = build_system_prompt(ReviewCategory.security).lower()

    assert "hardcoded secrets" in prompt


def test_secret_claims_drop_when_a_scanner_covers_them() -> None:
    """gitleaks reports committed secrets deterministically, and redaction has
    already stripped them from the diff — so the lens cannot see what it is
    being asked to find, while the ask still costs tokens on every call."""
    narrowed = build_system_prompt(ReviewCategory.security, secret_scanning=False).lower()

    assert "hardcoded secrets" not in narrowed


def test_secret_narrowing_keeps_the_rest_of_the_security_lens() -> None:
    """Only the bullet a scanner owns goes; the OWASP checklist stays."""
    narrowed = build_system_prompt(ReviewCategory.security, secret_scanning=False).lower()

    assert "owasp" in narrowed
    assert "ssrf" in narrowed
    assert "injection" in narrowed
    # Secrets *reaching a log* are a different defect from a committed literal,
    # and no secret scanner reports it — it must survive the narrowing.
    assert "pii" in narrowed


def test_secret_narrowing_applies_to_the_lens_block_too() -> None:
    """The fast preset sends the lens as a user block, not a system prompt."""
    from lgtmaybe.engine.prompt import build_lens_block

    full = build_lens_block(ReviewCategory.security).lower()
    narrowed = build_lens_block(ReviewCategory.security, secret_scanning=False).lower()

    assert "hardcoded secrets" in full
    assert "hardcoded secrets" not in narrowed
