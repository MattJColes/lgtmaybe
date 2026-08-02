## Context

The review engine composes each `(batch, lens)` call from three messages: the shared system preamble (`prompt.build_shared_preamble`), a per-batch prefix (static-analysis hints + the wrapped diff), and the lens block. `LiteLLMProvider._with_cache_control` places cache breakpoints on the system message and the **last prefix block**, and a per-batch warm-up primer runs one lens alone so the rest read the prefix from cache. Any per-directory content has to enter that structure without disturbing it.

Two adjacent features already solve halves of this problem and are the shapes to reuse rather than re-derive: `engine.passes_path_filters` (glob matching with the `**/`-prefix nicety) and `retrieve.resolve_needs` (bounded, redacted, de-duplicated read-only file retrieval behind an injected fetcher).

## Goals / Non-Goals

**Goals:**

- Scope free-text instructions and reference-file context to path globs.
- Keep the delivery mechanism inside the existing prompt-cache contract — no extra cache writes, no adapter change.
- Inherit redaction, budget, and caps from existing code rather than adding a second retrieval path.
- Keep the trust boundary explicit: this is configuration, never PR-author content.

**Non-Goals:**

- Per-directory *lens sets*, severity floors, or any other `ReviewConfig` override. Instructions and context only; a per-directory config merge is a different, much larger feature.
- Bugbot-style `BUGBOT.md` walk-up discovery (see Decisions).
- A CLI flag or Action input. `directory_rules` is a list of objects, so it is YAML-only exactly like `finding_rules` and `extra_lenses`.

## Decisions

1. **One model, one module, zero new prompt plumbing.** `DirectoryRule` (`paths`, `instructions`, `context_files`) and `engine/directory.py` with three functions. `rules_for` calls `passes_path_filters(path, include=rule.paths, exclude=[])`, which gives the `**/`-prefix behaviour and — with `paths=[]` — the "applies everywhere" case for free, mirroring how an empty `FindingRuleMatch` selects every finding. `load_context_files` calls `retrieve.resolve_needs` with a local-filesystem fetcher, inheriting redaction (`engine/redact.py`), the token budget (`compress.count_tokens`), the `MAX_FETCH_FILES` cap, and de-duplication.

2. **The block joins the prefix string, not a fourth message.** `_review_lens` builds `prefix = "\n\n".join(part for part in (dir_block, hint_block, wrapped) if part is not None)`. The directory block varies per batch exactly like the hints block already does, so it is warmed once by the existing primer and read from cache by lenses 2..N. `build_shared_preamble` is untouched, which keeps the cross-batch system cache entry and the byte-identical-when-unset guarantee. The adapter needs no change: it still marks the last prefix block.

3. **Context comes from the workspace, never the gateway.** `Path.cwd()`, read directly. On `pull_request_target` the example workflows check out with no `ref` — the base branch — which is where `.lgtmaybe.yml` and `lens_paths` already come from, so this inherits the existing trust property rather than inventing one. `github.get_file_contents` resolves at the PR head and must never be involved; a test asserts no gateway fetcher is called while a context file is loaded. The fetcher additionally rejects a path whose resolved target escapes the root — config is trusted, but that check is two lines.

4. **Instructions are trusted; context text is neutralised.** Instructions ride verbatim under a trusted-configuration lead-in that reuses the wording of `prompt._LENS_LEAD_IN`, which already teaches the model the trust boundary between adjacent blocks. Context file text is passed through `injection.neutralise` — the same treatment `reflect._grounding_block` gives fetched file text — so a repository file quoting a delimiter cannot close a block early. No new marker family is needed.

5. **No `BUGBOT.md` walk-up.** Discovery-by-convention would add a filesystem walk, size and count caps, an ancestor-precedence rule, and a documentation burden about a file that *looks* PR-editable but is not (on `pull_request_target` it is read from the base) — all to express what `directory_rules` says in six lines of YAML with an explicit glob. It stays available later as a pure loader desugar: `**/LGTMAYBE.md` → `DirectoryRule(paths=["<dir>/**"], instructions=<text>)`, needing no engine change at all.

## Risks / Trade-offs

- **[Context files eat the input budget]** → They get `max_input_tokens // 8` and at most `MAX_FETCH_FILES` files; the diff always dominates the call.
- **[A rule with wide globs adds tokens to every batch]** → The cost is visible (`--profile`, the `provider call` log lines) and the block rides the cached prefix, so it is paid roughly once per batch, not once per lens.
- **[A typo'd `context_files` path silently contributes nothing]** → Deliberate: a context file is an aid, and failing a whole review over a stale path is worse. The behaviour is documented.
- **[Rules do not compose into a per-directory config]** → Accepted for now; instructions cover the demand this feature was asked for, and a real config merge can be layered on later without changing this surface.
