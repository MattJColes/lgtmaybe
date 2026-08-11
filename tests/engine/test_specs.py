"""Tests for the spec lens: detecting a committed spec and checking the PR delivers it.

Three spec-driven workflows commit their specs into the repo — OpenSpec
(``openspec/``), GitHub Spec Kit (``.specify/`` + ``specs/NNN-slug/``) and Kiro
(``.kiro/specs/<feature>/``). Detection is a deterministic filesystem probe;
selection ranks the candidate spec directories against the PR so a monorepo with
forty specs sends at most a couple; the ticked-checkbox extractor turns "did the
PR deliver the spec?" into the precise list of claims the author made *in this
PR*.

Nothing detected, or nothing matched, means the lens is skipped entirely — the
same silence the intent lens keeps when no intent is stated.
"""

from __future__ import annotations

from pathlib import Path

from lgtmaybe.engine.specs import (
    SpecBundle,
    SpecSystem,
    build_spec_text,
    detect,
    load_spec_files,
    select,
    ticked_tasks,
)


def _write(root: Path, path: str, text: str = "placeholder\n") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class TestDetect:
    def test_no_spec_system_detects_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/app.py", "x = 1\n")
        _write(tmp_path, "docs/readme.md")
        assert detect(tmp_path) == []

    def test_openspec_change_proposal(self, tmp_path: Path) -> None:
        _write(tmp_path, "openspec/changes/add-payments/proposal.md")
        _write(tmp_path, "openspec/changes/add-payments/tasks.md")
        _write(tmp_path, "openspec/changes/add-payments/specs/billing/spec.md")

        bundles = detect(tmp_path)

        assert [b.system for b in bundles] == [SpecSystem.openspec]
        assert bundles[0].slug == "add-payments"
        assert bundles[0].root == "openspec/changes/add-payments"
        assert "openspec/changes/add-payments/proposal.md" in bundles[0].files
        assert "openspec/changes/add-payments/tasks.md" in bundles[0].files
        assert "openspec/changes/add-payments/specs/billing/spec.md" in bundles[0].files

    def test_openspec_archive_is_not_an_active_change(self, tmp_path: Path) -> None:
        _write(tmp_path, "openspec/changes/archive/2026-01-01-done/proposal.md")
        _write(tmp_path, "openspec/changes/archive/2026-01-01-done/tasks.md")

        assert detect(tmp_path) == []

    def test_openspec_living_specs(self, tmp_path: Path) -> None:
        _write(tmp_path, "openspec/specs/billing/spec.md")
        _write(tmp_path, "openspec/specs/billing/anchors.yml")

        bundles = detect(tmp_path)

        assert [(b.system, b.slug) for b in bundles] == [(SpecSystem.openspec, "billing")]
        # The anchors sidecar is machine config, not a requirement statement.
        assert bundles[0].files == ("openspec/specs/billing/spec.md",)

    def test_speckit(self, tmp_path: Path) -> None:
        _write(tmp_path, ".specify/memory/constitution.md")
        _write(tmp_path, "specs/003-payment-links/spec.md")
        _write(tmp_path, "specs/003-payment-links/plan.md")
        _write(tmp_path, "specs/003-payment-links/tasks.md")

        bundles = detect(tmp_path)

        assert [(b.system, b.slug) for b in bundles] == [(SpecSystem.speckit, "003-payment-links")]
        assert bundles[0].files == (
            "specs/003-payment-links/spec.md",
            "specs/003-payment-links/plan.md",
            "specs/003-payment-links/tasks.md",
        )

    def test_speckit_without_specify_dir_still_detects_on_spec_plus_plan(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "specs/001-thing/spec.md")
        _write(tmp_path, "specs/001-thing/plan.md")

        assert [b.slug for b in detect(tmp_path)] == ["001-thing"]

    def test_a_bare_specs_dir_is_not_speckit(self, tmp_path: Path) -> None:
        # A `specs/` directory of prose with no plan.md is not a Spec Kit tree;
        # claiming it is would fire the lens on every docs repo.
        _write(tmp_path, "specs/overview/spec.md")

        assert detect(tmp_path) == []

    def test_kiro(self, tmp_path: Path) -> None:
        _write(tmp_path, ".kiro/specs/checkout-flow/requirements.md")
        _write(tmp_path, ".kiro/specs/checkout-flow/design.md")
        _write(tmp_path, ".kiro/specs/checkout-flow/tasks.md")

        bundles = detect(tmp_path)

        assert [(b.system, b.slug) for b in bundles] == [(SpecSystem.kiro, "checkout-flow")]
        assert bundles[0].files == (
            ".kiro/specs/checkout-flow/requirements.md",
            ".kiro/specs/checkout-flow/design.md",
            ".kiro/specs/checkout-flow/tasks.md",
        )

    def test_several_systems_coexist(self, tmp_path: Path) -> None:
        _write(tmp_path, "openspec/changes/one/proposal.md")
        _write(tmp_path, ".kiro/specs/two/requirements.md")

        assert {b.system for b in detect(tmp_path)} == {SpecSystem.openspec, SpecSystem.kiro}

    def test_extra_paths_pick_up_a_custom_layout(self, tmp_path: Path) -> None:
        _write(tmp_path, "design/rfcs/rfc-7/requirements.md")
        _write(tmp_path, "design/rfcs/rfc-7/tasks.md")

        bundles = detect(tmp_path, extra_paths=["design/rfcs/*"])

        assert [b.slug for b in bundles] == ["rfc-7"]
        assert bundles[0].files == (
            "design/rfcs/rfc-7/requirements.md",
            "design/rfcs/rfc-7/tasks.md",
        )

    def test_a_spec_dir_is_skipped_when_it_holds_no_spec_files(self, tmp_path: Path) -> None:
        (tmp_path / ".kiro" / "specs" / "empty").mkdir(parents=True)

        assert detect(tmp_path) == []


class TestSelect:
    def _bundle(self, slug: str, *files: str) -> SpecBundle:
        return SpecBundle(
            system=SpecSystem.kiro,
            slug=slug,
            root=f".kiro/specs/{slug}",
            files=files or (f".kiro/specs/{slug}/requirements.md",),
        )

    def test_a_spec_the_pr_changes_wins(self) -> None:
        a, b = self._bundle("alpha"), self._bundle("beta")

        selected = select(
            [a, b],
            changed_files=[".kiro/specs/beta/tasks.md", "src/beta.py"],
            branch="",
            intent_text="",
        )

        assert [s.slug for s in selected] == ["beta"]

    def test_branch_name_matches_the_slug(self) -> None:
        a, b = self._bundle("003-payment-links"), self._bundle("004-refunds")

        selected = select(
            [a, b], changed_files=["src/pay.py"], branch="004-refunds", intent_text=""
        )

        assert [s.slug for s in selected] == ["004-refunds"]

    def test_intent_text_naming_the_slug_matches(self) -> None:
        a, b = self._bundle("alpha"), self._bundle("beta")

        selected = select(
            [a, b],
            changed_files=["src/x.py"],
            branch="feature",
            intent_text="Title: implement beta\n\nDescription:\nthe beta spec",
        )

        assert [s.slug for s in selected] == ["beta"]

    def test_nothing_relevant_selects_nothing(self) -> None:
        bundles = [self._bundle("alpha"), self._bundle("beta")]

        assert (
            select(bundles, changed_files=["src/unrelated.py"], branch="chore/bump", intent_text="")
            == []
        )

    def test_a_lone_spec_is_selected_when_the_pr_changes_code(self) -> None:
        # One spec in the repo and a code change: it is the only thing the PR
        # could be delivering, so ambiguity is not a reason to stay silent.
        only = self._bundle("alpha")

        selected = select([only], changed_files=["src/x.py"], branch="chore/bump", intent_text="")

        assert [s.slug for s in selected] == ["alpha"]

    def test_selection_is_capped(self) -> None:
        bundles = [self._bundle(f"spec-{n}") for n in range(10)]
        changed = [f".kiro/specs/spec-{n}/tasks.md" for n in range(10)]

        selected = select(bundles, changed_files=changed, branch="", intent_text="")

        assert len(selected) <= 2

    def test_ranking_is_deterministic_for_equal_scores(self) -> None:
        bundles = [self._bundle("beta"), self._bundle("alpha")]
        changed = [".kiro/specs/beta/tasks.md", ".kiro/specs/alpha/tasks.md"]

        first = select(bundles, changed_files=changed, branch="", intent_text="")
        second = select(bundles, changed_files=changed, branch="", intent_text="")

        assert [s.slug for s in first] == [s.slug for s in second]


class TestTickedTasks:
    def test_extracts_a_task_the_pr_ticked_off(self) -> None:
        diff = (
            "diff --git a/specs/003-x/tasks.md b/specs/003-x/tasks.md\n"
            "--- a/specs/003-x/tasks.md\n"
            "+++ b/specs/003-x/tasks.md\n"
            "@@ -10,3 +10,3 @@\n"
            "-- [ ] T014 [US1] Implement PaymentService in src/services/payment.py\n"
            "+- [x] T014 [US1] Implement PaymentService in src/services/payment.py\n"
            " - [ ] T015 Wire the route\n"
        )

        assert ticked_tasks(diff) == [
            "T014 [US1] Implement PaymentService in src/services/payment.py"
        ]

    def test_a_task_that_stays_unticked_is_not_a_claim(self) -> None:
        diff = (
            "diff --git a/tasks.md b/tasks.md\n"
            "--- a/tasks.md\n+++ b/tasks.md\n@@ -1,2 +1,3 @@\n"
            "+- [ ] 2.1 Not done yet\n"
        )

        assert ticked_tasks(diff) == []

    def test_a_task_added_already_ticked_is_a_claim(self) -> None:
        diff = (
            "diff --git a/tasks.md b/tasks.md\n"
            "--- a/tasks.md\n+++ b/tasks.md\n@@ -1,2 +1,3 @@\n"
            "+- [x] 2.1 Add the migration\n"
        )

        assert ticked_tasks(diff) == ["2.1 Add the migration"]

    def test_a_task_already_ticked_before_the_pr_is_not_a_claim(self) -> None:
        diff = (
            "diff --git a/tasks.md b/tasks.md\n"
            "--- a/tasks.md\n+++ b/tasks.md\n@@ -1,3 +1,3 @@\n"
            " - [x] 1.1 Done in an earlier PR\n"
            "+- [x] 1.2 Done in this one\n"
        )

        assert ticked_tasks(diff) == ["1.2 Done in this one"]

    def test_checkboxes_outside_a_task_file_are_ignored(self) -> None:
        # A PR template or a README checklist is not a delivery claim.
        diff = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n+++ b/README.md\n@@ -1,2 +1,3 @@\n"
            "+- [x] Supports dark mode\n"
        )

        assert ticked_tasks(diff) == []

    def test_uppercase_marker_and_indentation_are_handled(self) -> None:
        diff = (
            "diff --git a/openspec/changes/x/tasks.md b/openspec/changes/x/tasks.md\n"
            "--- a/openspec/changes/x/tasks.md\n+++ b/openspec/changes/x/tasks.md\n"
            "@@ -1,2 +1,3 @@\n"
            "+  - [X] 3.2 Nested subtask\n"
        )

        assert ticked_tasks(diff) == ["3.2 Nested subtask"]

    def test_no_diff_no_claims(self) -> None:
        assert ticked_tasks("") == []


class TestLoadSpecFiles:
    def test_reads_from_the_workspace(self, tmp_path: Path) -> None:
        _write(tmp_path, ".kiro/specs/a/requirements.md", "WHEN x THEN SHALL y\n")
        bundle = SpecBundle(
            system=SpecSystem.kiro,
            slug="a",
            root=".kiro/specs/a",
            files=(".kiro/specs/a/requirements.md",),
        )

        loaded = load_spec_files([bundle], root=tmp_path, head_texts={}, budget_tokens=10_000)

        assert loaded == {".kiro/specs/a/requirements.md": "WHEN x THEN SHALL y\n"}

    def test_head_text_wins_for_a_file_the_pr_changed(self, tmp_path: Path) -> None:
        # The base branch has the old spec; the PR adds a requirement. The lens
        # must judge against the version the PR is delivering, not the base one.
        _write(tmp_path, ".kiro/specs/a/requirements.md", "old requirement\n")
        bundle = SpecBundle(
            system=SpecSystem.kiro,
            slug="a",
            root=".kiro/specs/a",
            files=(".kiro/specs/a/requirements.md",),
        )

        loaded = load_spec_files(
            [bundle],
            root=tmp_path,
            head_texts={".kiro/specs/a/requirements.md": "new requirement\n"},
            budget_tokens=10_000,
        )

        assert loaded == {".kiro/specs/a/requirements.md": "new requirement\n"}

    def test_a_spec_added_by_the_pr_is_readable_though_absent_from_the_workspace(
        self, tmp_path: Path
    ) -> None:
        bundle = SpecBundle(
            system=SpecSystem.kiro,
            slug="a",
            root=".kiro/specs/a",
            files=(".kiro/specs/a/requirements.md",),
        )

        loaded = load_spec_files(
            [bundle],
            root=tmp_path,
            head_texts={".kiro/specs/a/requirements.md": "brand new\n"},
            budget_tokens=10_000,
        )

        assert loaded == {".kiro/specs/a/requirements.md": "brand new\n"}

    def test_secrets_in_spec_text_are_redacted(self, tmp_path: Path) -> None:
        from lgtmaybe.engine.redact import REDACTED_PLACEHOLDER

        _write(
            tmp_path,
            ".kiro/specs/a/design.md",
            'The service uses password = "hunter2000shhh" to connect.\n',
        )
        bundle = SpecBundle(
            system=SpecSystem.kiro,
            slug="a",
            root=".kiro/specs/a",
            files=(".kiro/specs/a/design.md",),
        )

        loaded = load_spec_files([bundle], root=tmp_path, head_texts={}, budget_tokens=10_000)

        assert "hunter2000shhh" not in loaded[".kiro/specs/a/design.md"]
        assert REDACTED_PLACEHOLDER in loaded[".kiro/specs/a/design.md"]

    def test_a_path_climbing_out_of_the_workspace_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "secrets.md"
        outside.write_text("nope\n", encoding="utf-8")
        workspace = tmp_path / "repo"
        workspace.mkdir()
        bundle = SpecBundle(
            system=SpecSystem.kiro,
            slug="a",
            root=".kiro/specs/a",
            files=("../secrets.md",),
        )

        loaded = load_spec_files([bundle], root=workspace, head_texts={}, budget_tokens=10_000)

        assert loaded == {}

    def test_an_unreadable_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        _write(tmp_path, ".kiro/specs/a/requirements.md", "here\n")
        bundle = SpecBundle(
            system=SpecSystem.kiro,
            slug="a",
            root=".kiro/specs/a",
            files=(".kiro/specs/a/requirements.md", ".kiro/specs/a/missing.md"),
        )

        loaded = load_spec_files([bundle], root=tmp_path, head_texts={}, budget_tokens=10_000)

        assert list(loaded) == [".kiro/specs/a/requirements.md"]

    def test_the_token_budget_is_respected(self, tmp_path: Path) -> None:
        _write(tmp_path, ".kiro/specs/a/requirements.md", "word " * 5_000)
        _write(tmp_path, ".kiro/specs/a/design.md", "word " * 5_000)
        bundle = SpecBundle(
            system=SpecSystem.kiro,
            slug="a",
            root=".kiro/specs/a",
            files=(".kiro/specs/a/requirements.md", ".kiro/specs/a/design.md"),
        )

        loaded = load_spec_files([bundle], root=tmp_path, head_texts={}, budget_tokens=100)

        assert len(loaded) < 2


class TestBuildSpecText:
    def _bundle(self) -> SpecBundle:
        return SpecBundle(
            system=SpecSystem.kiro,
            slug="checkout",
            root=".kiro/specs/checkout",
            files=(".kiro/specs/checkout/requirements.md",),
        )

    def test_renders_the_spec_system_slug_and_file_text(self) -> None:
        text = build_spec_text(
            [self._bundle()],
            {".kiro/specs/checkout/requirements.md": "WHEN paid THEN SHALL email\n"},
            claims=[],
        )

        assert text is not None
        assert "kiro" in text
        assert "checkout" in text
        assert ".kiro/specs/checkout/requirements.md" in text
        assert "WHEN paid THEN SHALL email" in text

    def test_ticked_tasks_are_rendered_as_explicit_claims(self) -> None:
        text = build_spec_text(
            [self._bundle()],
            {".kiro/specs/checkout/requirements.md": "req\n"},
            claims=["T014 Implement PaymentService in src/services/payment.py"],
        )

        assert text is not None
        assert "T014 Implement PaymentService" in text

    def test_no_content_renders_nothing(self) -> None:
        assert build_spec_text([], {}, claims=[]) is None

    def test_claims_alone_are_not_enough_without_spec_text(self) -> None:
        # A ticked checkbox with no readable spec gives the lens nothing to
        # judge against; better silence than a call that can only guess.
        assert build_spec_text([self._bundle()], {}, claims=["1.1 Do a thing"]) is None
