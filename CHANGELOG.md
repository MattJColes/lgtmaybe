# Changelog

## [2.5.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.5.0...lgtmaybe-v2.5.1) (2026-08-24)


### Bug Fixes

* **provider:** send bedrock a schema subset its validator accepts ([#533](https://github.com/MattJColes/lgtmaybe/issues/533)) ([91a8cfc](https://github.com/MattJColes/lgtmaybe/commit/91a8cfcd8c92741b8ba04a3b26680260c90053ca)), closes [#531](https://github.com/MattJColes/lgtmaybe/issues/531)


### Dependencies

* bump the python-dependencies group with 9 updates ([#530](https://github.com/MattJColes/lgtmaybe/issues/530)) ([dfe686c](https://github.com/MattJColes/lgtmaybe/commit/dfe686c2f605c0f8e4131091a3213bf357631a98))

## [2.5.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.4.0...lgtmaybe-v2.5.0) (2026-08-20)


### Features

* hold the advisory lenses to the severity their prompt asks for ([#529](https://github.com/MattJColes/lgtmaybe/issues/529)) ([c9aab3c](https://github.com/MattJColes/lgtmaybe/commit/c9aab3cee44d673d73d4e48ece81f07d073a2d2b))


### Bug Fixes

* count tokens in the model's own tokenizer and reserve prompt overhead ([#528](https://github.com/MattJColes/lgtmaybe/issues/528)) ([d41dab9](https://github.com/MattJColes/lgtmaybe/commit/d41dab9418985564ebed397e132a93ccf3932c5f)), closes [#509](https://github.com/MattJColes/lgtmaybe/issues/509)
* escape the file path when fetching file contents ([#522](https://github.com/MattJColes/lgtmaybe/issues/522)) ([c244bf7](https://github.com/MattJColes/lgtmaybe/commit/c244bf75b3b645fdb4a0d9f7e82b21265608fc89)), closes [#508](https://github.com/MattJColes/lgtmaybe/issues/508)
* honour resolve_fixed on GitLab ([#525](https://github.com/MattJColes/lgtmaybe/issues/525)) ([6f5efe5](https://github.com/MattJColes/lgtmaybe/commit/6f5efe5193907425c5ee1ed9037b12e77bda94e4)), closes [#502](https://github.com/MattJColes/lgtmaybe/issues/502)
* read a fenced reply in /ask and follow-up validation ([#523](https://github.com/MattJColes/lgtmaybe/issues/523)) ([ae4e650](https://github.com/MattJColes/lgtmaybe/commit/ae4e650f6a2034214a8e321b7be089cff777cba3)), closes [#510](https://github.com/MattJColes/lgtmaybe/issues/510) [#511](https://github.com/MattJColes/lgtmaybe/issues/511)
* read a self-hosted server's tool-call rejection wording ([#526](https://github.com/MattJColes/lgtmaybe/issues/526)) ([3e3a200](https://github.com/MattJColes/lgtmaybe/commit/3e3a20007a5d94b0d6cdb610596ff6e73f1ef820)), closes [#499](https://github.com/MattJColes/lgtmaybe/issues/499)


### Performance Improvements

* overlap the per-file boundary scans and guard the flood counter ([#524](https://github.com/MattJColes/lgtmaybe/issues/524)) ([70b87e5](https://github.com/MattJColes/lgtmaybe/commit/70b87e554002ebe25be0ab4e5b824e56c6b28782)), closes [#505](https://github.com/MattJColes/lgtmaybe/issues/505) [#506](https://github.com/MattJColes/lgtmaybe/issues/506)


### Documentation

* correct the gitea token scopes and drop a flag that does not exist ([#527](https://github.com/MattJColes/lgtmaybe/issues/527)) ([fd36563](https://github.com/MattJColes/lgtmaybe/commit/fd36563a21daa68c8899863f54ec411f8a963508)), closes [#500](https://github.com/MattJColes/lgtmaybe/issues/500) [#501](https://github.com/MattJColes/lgtmaybe/issues/501)
* rescore the model-choice tables and correct three recommendations ([#512](https://github.com/MattJColes/lgtmaybe/issues/512)) ([d9953cc](https://github.com/MattJColes/lgtmaybe/commit/d9953ccdbd568e022d345aabf7656f83e93d74a0))

## [2.4.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.3.0...lgtmaybe-v2.4.0) (2026-08-19)


### Features

* review merge requests on GitLab ([#482](https://github.com/MattJColes/lgtmaybe/issues/482)) ([adb87b9](https://github.com/MattJColes/lgtmaybe/commit/adb87b9c3bd6936467d3a21841a74842b167d61a))
* review pull requests on Gitea ([#481](https://github.com/MattJColes/lgtmaybe/issues/481)) ([b46d34a](https://github.com/MattJColes/lgtmaybe/commit/b46d34a7d2762beac567a00cf114796ca5df040d))


### Bug Fixes

* bound runaway model output that floods a review ([#484](https://github.com/MattJColes/lgtmaybe/issues/484)) ([90f1511](https://github.com/MattJColes/lgtmaybe/commit/90f1511773e1c366190b284bfcafcb48f53cbf0b))


### Documentation

* add benchmark model selection guide ([#472](https://github.com/MattJColes/lgtmaybe/issues/472)) ([9a47534](https://github.com/MattJColes/lgtmaybe/commit/9a475349edcfcfaf20bdb3bac114355f83463c3f))
* cover GitLab and Gitea across the documentation ([#483](https://github.com/MattJColes/lgtmaybe/issues/483)) ([baf0660](https://github.com/MattJColes/lgtmaybe/commit/baf0660a07d1964d6a28d35fb9b0a97299d05ff0))
* cut table-restating detail from the review-model notes ([#478](https://github.com/MattJColes/lgtmaybe/issues/478)) ([adfb0a9](https://github.com/MattJColes/lgtmaybe/commit/adfb0a923c7cf16592a34bb287cf9dbc34a674eb))
* define the benchmark suites before the tables that use them ([#479](https://github.com/MattJColes/lgtmaybe/issues/479)) ([4d30161](https://github.com/MattJColes/lgtmaybe/commit/4d3016111faef896a50bbe3d8b54c0331ef71a46))
* make the review-model guide read more naturally ([#475](https://github.com/MattJColes/lgtmaybe/issues/475)) ([0bf32a7](https://github.com/MattJColes/lgtmaybe/commit/0bf32a772214461998258e4e54981ea079dcc668))
* plainer opening for the choose-a-review-model page ([#476](https://github.com/MattJColes/lgtmaybe/issues/476)) ([364f1a6](https://github.com/MattJColes/lgtmaybe/commit/364f1a6628e82da44f861637176df0f48ea9ab30))
* remove marketing phrasing from the review-model guide ([#477](https://github.com/MattJColes/lgtmaybe/issues/477)) ([2b45195](https://github.com/MattJColes/lgtmaybe/commit/2b45195f6352242af57281233e9d1678ce5bd3d1))

## [2.3.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.2.0...lgtmaybe-v2.3.0) (2026-08-18)


### Features

* **provider:** keep structured output on routes that reject response_format ([#470](https://github.com/MattJColes/lgtmaybe/issues/470)) ([28830c2](https://github.com/MattJColes/lgtmaybe/commit/28830c23e19dfbd895a1d357a094fb8a78e0eba5))

## [2.2.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.1.4...lgtmaybe-v2.2.0) (2026-08-17)


### Features

* **cli:** give the profile a machine-readable form, and stop it corrupting stdout ([#460](https://github.com/MattJColes/lgtmaybe/issues/460)) ([ff86872](https://github.com/MattJColes/lgtmaybe/commit/ff86872d1fcda2b93b27f374b90561336c1ed98d))
* **engine:** re-ask a lens without the schema when its reply won't parse ([#463](https://github.com/MattJColes/lgtmaybe/issues/463)) ([2a241cd](https://github.com/MattJColes/lgtmaybe/commit/2a241cd723512a80a27030fbce252c6851afc506)), closes [#454](https://github.com/MattJColes/lgtmaybe/issues/454)
* **evals:** run each fixture N times so flaky and reproducible failures separate ([#461](https://github.com/MattJColes/lgtmaybe/issues/461)) ([b8d0568](https://github.com/MattJColes/lgtmaybe/commit/b8d0568187c608fc474b07d9ebe0267d3b48589e)), closes [#458](https://github.com/MattJColes/lgtmaybe/issues/458)
* make structured-output compliance failures diagnosable and survivable ([#450](https://github.com/MattJColes/lgtmaybe/issues/450)) ([d1c4f4d](https://github.com/MattJColes/lgtmaybe/commit/d1c4f4d0dc7389ff72412dca30d179d53693b96b))


### Bug Fixes

* **engine:** don't re-ask a lens whose schema the adapter already stripped ([#465](https://github.com/MattJColes/lgtmaybe/issues/465)) ([60d7944](https://github.com/MattJColes/lgtmaybe/commit/60d79447b0f647f5406c493204bafbc4ee2796ab))
* **provider:** don't step down an effort the route would discard ([#459](https://github.com/MattJColes/lgtmaybe/issues/459)) ([464b5b6](https://github.com/MattJColes/lgtmaybe/commit/464b5b65099af7fda943ee905513787379adea60))
* **provider:** give a reasoning-bound truncation a lever when no effort was set ([#452](https://github.com/MattJColes/lgtmaybe/issues/452)) ([abe7dac](https://github.com/MattJColes/lgtmaybe/commit/abe7dac224caa5f87e70d546726acb030bd52308))


### Dependencies

* bump the python-dependencies group with 8 updates ([#449](https://github.com/MattJColes/lgtmaybe/issues/449)) ([047e8b4](https://github.com/MattJColes/lgtmaybe/commit/047e8b4302e17f57ed9d2137963d9c2a4f69f471))

## [2.1.4](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.1.3...lgtmaybe-v2.1.4) (2026-08-14)


### Bug Fixes

* **release:** make the homebrew gate wait for the PyPI publish ([#447](https://github.com/MattJColes/lgtmaybe/issues/447)) ([1806faf](https://github.com/MattJColes/lgtmaybe/commit/1806fafaf1415c158c067cc6fed54d11fa3f0828))

## [2.1.3](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.1.2...lgtmaybe-v2.1.3) (2026-08-14)


### Bug Fixes

* **github:** post re-run inline findings when the run was incomplete ([#445](https://github.com/MattJColes/lgtmaybe/issues/445)) ([d145b70](https://github.com/MattJColes/lgtmaybe/commit/d145b70aa6bf0ca8a8d6889efaf57dd8b59b0374))

## [2.1.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.1.1...lgtmaybe-v2.1.2) (2026-08-14)


### Bug Fixes

* trace review findings through profile ([#441](https://github.com/MattJColes/lgtmaybe/issues/441)) ([8f21f4d](https://github.com/MattJColes/lgtmaybe/commit/8f21f4db2c8f4df6a56d2acae6e0a603f63aef77))

## [2.1.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.1.0...lgtmaybe-v2.1.1) (2026-08-14)


### Bug Fixes

* **github:** preserve findings rejected by GitHub ([#438](https://github.com/MattJColes/lgtmaybe/issues/438)) ([801d195](https://github.com/MattJColes/lgtmaybe/commit/801d195cbc1a78af3ac396a24d86571f8791b0e4))

## [2.1.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.0.1...lgtmaybe-v2.1.0) (2026-08-13)


### Features

* **engine:** retry a reasoning-bound truncation at a lower effort ([#434](https://github.com/MattJColes/lgtmaybe/issues/434)) ([83e3736](https://github.com/MattJColes/lgtmaybe/commit/83e37366d3909c2a63e56b19ef410765f6b10c3e))
* **engine:** tell every lens what it was not shown ([#433](https://github.com/MattJColes/lgtmaybe/issues/433)) ([495bf40](https://github.com/MattJColes/lgtmaybe/commit/495bf40eb9884e29a6197180aa74b2aabf4e0e9d))
* **profiling:** report the reasoning share of every call, not just failed ones ([#436](https://github.com/MattJColes/lgtmaybe/issues/436)) ([35fa139](https://github.com/MattJColes/lgtmaybe/commit/35fa139e97bc66b5b3aed50e3ad83fb6f98b7cca))

## [2.0.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v2.0.0...lgtmaybe-v2.0.1) (2026-08-13)


### Bug Fixes

* align GitHub Action with v2 ([#430](https://github.com/MattJColes/lgtmaybe/issues/430)) ([386b9d4](https://github.com/MattJColes/lgtmaybe/commit/386b9d4c250c5adeccf05b9876aed3ebb0f7f0f5))
* **prompt:** close the seam between humility and the gap carve-out ([#428](https://github.com/MattJColes/lgtmaybe/issues/428)) ([0d09eb4](https://github.com/MattJColes/lgtmaybe/commit/0d09eb4a06c45cf78c7414501e9a3b5a3380b707))

## [2.0.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.14.1...lgtmaybe-v2.0.0) (2026-08-13)


### ⚠ BREAKING CHANGES

* remove answer_replies from configuration and Action inputs.
* remove legacy prompt cache path ([#399](https://github.com/MattJColes/lgtmaybe/issues/399))

### Features

* clarify user-facing review responses ([#405](https://github.com/MattJColes/lgtmaybe/issues/405)) ([59a8b64](https://github.com/MattJColes/lgtmaybe/commit/59a8b64e62e41c43bdb57070b15957e0f997e2d4))
* **cli:** report the running version with --version ([#411](https://github.com/MattJColes/lgtmaybe/issues/411)) ([ac15398](https://github.com/MattJColes/lgtmaybe/commit/ac153987ccb3a315d1706a029f3e511feec40e7a))
* default to medium reasoning, and stop capping local models ([#402](https://github.com/MattJColes/lgtmaybe/issues/402)) ([3028133](https://github.com/MattJColes/lgtmaybe/commit/3028133f245340b2d73916439e5873382f42f4ac))
* **ollama:** cap local output so a review can't decode forever ([#413](https://github.com/MattJColes/lgtmaybe/issues/413)) ([c0c7428](https://github.com/MattJColes/lgtmaybe/commit/c0c7428ebf246f2f23a2251363d4afb1163ea8d2))
* validate findings on follow-up reviews ([#412](https://github.com/MattJColes/lgtmaybe/issues/412)) ([419460a](https://github.com/MattJColes/lgtmaybe/commit/419460a12fed5794f819934414ec37a660ca5889))


### Bug Fixes

* **cli:** scale the local per-call timeout by the fan-out width ([#404](https://github.com/MattJColes/lgtmaybe/issues/404)) ([3385dcd](https://github.com/MattJColes/lgtmaybe/commit/3385dcdd6acff3a1764c19f4ac922bedba5e45bf))
* deterministic ceiling tests, and delete the dead single-stream branch ([#407](https://github.com/MattJColes/lgtmaybe/issues/407)) ([334396e](https://github.com/MattJColes/lgtmaybe/commit/334396ece4af8380e075d5a1009eaf52cf59568a))
* **engine:** rescue a split piece that failed on the provider ([#400](https://github.com/MattJColes/lgtmaybe/issues/400)) ([160fcb0](https://github.com/MattJColes/lgtmaybe/commit/160fcb0a2b7f8e0cb490bfde15317a40880f5b82))
* give a reasoning-bound truncation both levers, and the dogfood cap the headroom ([#418](https://github.com/MattJColes/lgtmaybe/issues/418)) ([ef74cfe](https://github.com/MattJColes/lgtmaybe/commit/ef74cfe4747e56125de17ce6ae1fb690b1e06bee))
* make follow-up thread resolution fail safe ([#419](https://github.com/MattJColes/lgtmaybe/issues/419)) ([ccd0e62](https://github.com/MattJColes/lgtmaybe/commit/ccd0e62cca72d854c094e3cf869ed644f6d63c3d))
* **providers:** read a spent ceiling as a truncation ([#417](https://github.com/MattJColes/lgtmaybe/issues/417)) ([64513ce](https://github.com/MattJColes/lgtmaybe/commit/64513cea4de117f963ec8830b5183d5cf27cc051))
* remove automatic review thread replies ([#408](https://github.com/MattJColes/lgtmaybe/issues/408)) ([d435124](https://github.com/MattJColes/lgtmaybe/commit/d4351244dc1768301da99e232f31c70889b61fa5))
* restore automatic PR diagrams ([#406](https://github.com/MattJColes/lgtmaybe/issues/406)) ([183b8ec](https://github.com/MattJColes/lgtmaybe/commit/183b8ec6080b3f09ea2835ce19f480340e596806))
* **slash:** stop /ask asking the model for a findings object ([#415](https://github.com/MattJColes/lgtmaybe/issues/415)) ([800df1a](https://github.com/MattJColes/lgtmaybe/commit/800df1a533b47ba012ab9b8b37a8566f5074642b))


### Code Refactoring

* remove legacy prompt cache path ([#399](https://github.com/MattJColes/lgtmaybe/issues/399)) ([86f276e](https://github.com/MattJColes/lgtmaybe/commit/86f276ed2e16171f9b2007e875cf0a034c17635a))

## [1.14.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.14.0...lgtmaybe-v1.14.1) (2026-08-13)


### Dependencies

* bump the python-dependencies group with 8 updates ([#375](https://github.com/MattJColes/lgtmaybe/issues/375)) ([50056c6](https://github.com/MattJColes/lgtmaybe/commit/50056c6f4f666322c4d55a22ab54bb6922e0a594))

## [1.14.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.13.1...lgtmaybe-v1.14.0) (2026-08-13)


### Features

* **engine:** add a spec lens that checks the PR against its committed spec ([#376](https://github.com/MattJColes/lgtmaybe/issues/376)) ([d55e35e](https://github.com/MattJColes/lgtmaybe/commit/d55e35e7ceb7e7681bd8d463e70937b5e76e2c17))


### Bug Fixes

* stop one flaky provider call voiding a whole review round ([#378](https://github.com/MattJColes/lgtmaybe/issues/378)) ([6c59f86](https://github.com/MattJColes/lgtmaybe/commit/6c59f861e87b6e465a25275ed553c25335fb591f))


### Dependencies

* bump the python-dependencies group and unblock pip-audit on aiohttp ([#379](https://github.com/MattJColes/lgtmaybe/issues/379)) ([d296f25](https://github.com/MattJColes/lgtmaybe/commit/d296f252573109fa23a3682c9ef8018cba4a8e0d))

## [1.13.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.13.0...lgtmaybe-v1.13.1) (2026-08-04)


### Bug Fixes

* **github:** render finding confidence as a percentage ([#373](https://github.com/MattJColes/lgtmaybe/issues/373)) ([6b56e0f](https://github.com/MattJColes/lgtmaybe/commit/6b56e0fb513eb779c3b39c5157741db2a33a0b65))

## [1.13.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.12.2...lgtmaybe-v1.13.0) (2026-08-03)


### Features

* **cli:** post partial results when the process is signalled ([#367](https://github.com/MattJColes/lgtmaybe/issues/367)) ([1819896](https://github.com/MattJColes/lgtmaybe/commit/18198965ea30e54928f04b7f798852ddeaa91222))
* **engine:** skip oversized files and more generated artefacts ([#366](https://github.com/MattJColes/lgtmaybe/issues/366)) ([bb03da6](https://github.com/MattJColes/lgtmaybe/commit/bb03da602d6764f96ee5b6221abacb2c73f868f0))


### Bug Fixes

* **providers:** deliver the reasoning budget on OpenRouter, and never drop a param in silence ([#369](https://github.com/MattJColes/lgtmaybe/issues/369)) ([afcc0b8](https://github.com/MattJColes/lgtmaybe/commit/afcc0b89df3bedabf0ba26e7db8afb09d428f88a)), closes [#348](https://github.com/MattJColes/lgtmaybe/issues/348)


### Performance Improvements

* **ci:** start no job for a comment with no slash command ([#365](https://github.com/MattJColes/lgtmaybe/issues/365)) ([e72903d](https://github.com/MattJColes/lgtmaybe/commit/e72903dcbdfc12010b45a3a1bf246f013c744359))
* **engine:** stop splitting a truncation the thinking budget caused ([#368](https://github.com/MattJColes/lgtmaybe/issues/368)) ([85c5258](https://github.com/MattJColes/lgtmaybe/commit/85c52583c9094ae4ee7396e1f35fb34819b3a888))

## [1.12.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.12.1...lgtmaybe-v1.12.2) (2026-08-03)


### Dependencies

* bump litellm in the python-dependencies group ([#363](https://github.com/MattJColes/lgtmaybe/issues/363)) ([d225fcc](https://github.com/MattJColes/lgtmaybe/commit/d225fcc22570eff3c41ecb966eea629cc46937dc))

## [1.12.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.12.0...lgtmaybe-v1.12.1) (2026-08-03)


### Bug Fixes

* **deps:** sync uv.lock with the 1.12.0 version bump ([#340](https://github.com/MattJColes/lgtmaybe/issues/340)) ([b310637](https://github.com/MattJColes/lgtmaybe/commit/b31063763547d0b14da4b8c961b6426fde2c67a0))
* **engine:** exclude diff file headers from the triage line count ([#341](https://github.com/MattJColes/lgtmaybe/issues/341)) ([43965bb](https://github.com/MattJColes/lgtmaybe/commit/43965bb96f6c3a1829ae062eeed1889f07e81134)), closes [#326](https://github.com/MattJColes/lgtmaybe/issues/326)


### Dependencies

* bump the python-dependencies group across 1 directory with 7 updates ([#353](https://github.com/MattJColes/lgtmaybe/issues/353)) ([5c7f220](https://github.com/MattJColes/lgtmaybe/commit/5c7f220b3f39f2e1a0b7baf95b6629501b311dd9))

## [1.12.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.11.1...lgtmaybe-v1.12.0) (2026-08-02)


### Features

* **config:** bound reasoning with reasoning_effort ([#323](https://github.com/MattJColes/lgtmaybe/issues/323)) ([0e0c83c](https://github.com/MattJColes/lgtmaybe/commit/0e0c83c1d44905fb5d311cc06e03cee24d7a1694))

## [1.11.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.11.0...lgtmaybe-v1.11.1) (2026-08-02)


### Bug Fixes

* **engine:** tell the intent lens which files it was not shown ([#321](https://github.com/MattJColes/lgtmaybe/issues/321)) ([cfa8742](https://github.com/MattJColes/lgtmaybe/commit/cfa87423c2ac4ebdb38c81d8c0f1d0e88e2f39b6))

## [1.11.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.10.0...lgtmaybe-v1.11.0) (2026-08-02)


### Features

* **engine:** report reasoning tokens on every provider call ([#319](https://github.com/MattJColes/lgtmaybe/issues/319)) ([24c4645](https://github.com/MattJColes/lgtmaybe/commit/24c4645d8c2a288d5b2c7cc2964327b7d97a93ab))

## [1.10.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.9.1...lgtmaybe-v1.10.0) (2026-08-02)


### Features

* **cli:** flatten the diagram's collapsible blocks for a terminal ([#317](https://github.com/MattJColes/lgtmaybe/issues/317)) ([961738b](https://github.com/MattJColes/lgtmaybe/commit/961738be5db55ac2b1c4acf5ccf06428fc8f3a27))
* **config:** scope review instructions and context to a directory ([#314](https://github.com/MattJColes/lgtmaybe/issues/314)) ([d3f79f1](https://github.com/MattJColes/lgtmaybe/commit/d3f79f1cd62a6307fb6682797b42bb887e3d68fd))
* **diagram:** add a sequence view beside the change flowchart ([#309](https://github.com/MattJColes/lgtmaybe/issues/309)) ([3fe0f87](https://github.com/MattJColes/lgtmaybe/commit/3fe0f87a3c2aa287d8837c07abb204eb6a63d1ec))
* **engine:** let a lens defer once for bounded context ([#316](https://github.com/MattJColes/lgtmaybe/issues/316)) ([008eca7](https://github.com/MattJColes/lgtmaybe/commit/008eca70b2a3f9fc44ffe25e42d403be506d8d1a))
* **github:** show the lens and confidence on posted findings ([#313](https://github.com/MattJColes/lgtmaybe/issues/313)) ([f72d310](https://github.com/MattJColes/lgtmaybe/commit/f72d3109badfaba71ca6c9783cfde8abcb627f00))


### Bug Fixes

* **config:** stop the dogfood cap starving a reasoning model ([#312](https://github.com/MattJColes/lgtmaybe/issues/312)) ([a9e3593](https://github.com/MattJColes/lgtmaybe/commit/a9e3593d21de90cf73338139283eca27a1a81bfb))
* **engine:** split and retry when a call blows the output ceiling ([#315](https://github.com/MattJColes/lgtmaybe/issues/315)) ([dd4b3c9](https://github.com/MattJColes/lgtmaybe/commit/dd4b3c9a8f08ae8176214c6de4eb754dc673e72d))

## [1.9.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.9.0...lgtmaybe-v1.9.1) (2026-07-28)


### Bug Fixes

* **parse:** keep the findings a truncated response finished emitting ([#306](https://github.com/MattJColes/lgtmaybe/issues/306)) ([b8d9193](https://github.com/MattJColes/lgtmaybe/commit/b8d9193b759cb4fd7b728f777dee3a34636f6680))
* **provider:** report a truncated response as truncated, and stop retrying it ([#305](https://github.com/MattJColes/lgtmaybe/issues/305)) ([cadca72](https://github.com/MattJColes/lgtmaybe/commit/cadca72631537c128024e79b072675790acadd13))
* **provider:** survive a rejected response_format on Bedrock and keep --json stdout parseable ([#303](https://github.com/MattJColes/lgtmaybe/issues/303)) ([a11860c](https://github.com/MattJColes/lgtmaybe/commit/a11860c9a1eb99725ba27f53db8bb3197d9d1376))

## [1.9.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.8.1...lgtmaybe-v1.9.0) (2026-07-27)


### Features

* **cli:** report what a local review spent ([#299](https://github.com/MattJColes/lgtmaybe/issues/299)) ([78af30b](https://github.com/MattJColes/lgtmaybe/commit/78af30b3754915e0a7e18604170fd5898235253e))
* **engine:** cap a review's spend with max_review_tokens ([#297](https://github.com/MattJColes/lgtmaybe/issues/297)) ([78987a6](https://github.com/MattJColes/lgtmaybe/commit/78987a60d66afe7b77dda7252306ab8d83717dba))

## [1.8.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.8.0...lgtmaybe-v1.8.1) (2026-07-27)


### Bug Fixes

* bugs found driving the CLI end-to-end across 10 scenarios ([#291](https://github.com/MattJColes/lgtmaybe/issues/291)) ([1a9f0f0](https://github.com/MattJColes/lgtmaybe/commit/1a9f0f03d023c5e0a8e79515f088c57e6e6a6feb))
* **compress:** merge hunks whose context pads overlap ([#290](https://github.com/MattJColes/lgtmaybe/issues/290)) ([6fcb279](https://github.com/MattJColes/lgtmaybe/commit/6fcb279561229905ea6b9ef0b44943b58deb61ee))
* **context:** only pad to a definition that still contains the hunk ([#294](https://github.com/MattJColes/lgtmaybe/issues/294)) ([7cb9129](https://github.com/MattJColes/lgtmaybe/commit/7cb91294e502a7ad5cd0aed422dd2a5e78aec384))
* **identity:** stop asking for branded identity on an event that cannot mint it ([#296](https://github.com/MattJColes/lgtmaybe/issues/296)) ([f4c3bdd](https://github.com/MattJColes/lgtmaybe/commit/f4c3bdd279e6022c42a70328d94dd932fe16509d))
* **prompt:** stop asking lenses to flag our own redaction marker ([#293](https://github.com/MattJColes/lgtmaybe/issues/293)) ([054a4ce](https://github.com/MattJColes/lgtmaybe/commit/054a4ce1f363da2b85f167a673f9f9e5c0dbe7f0))


### Performance Improvements

* **context:** size the enclosing-definition reach against the fixed pad ([#295](https://github.com/MattJColes/lgtmaybe/issues/295)) ([eb586f1](https://github.com/MattJColes/lgtmaybe/commit/eb586f15752be418672ebef868c12d41b3bcb373))

## [1.8.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.7.0...lgtmaybe-v1.8.0) (2026-07-27)


### Features

* **engine:** name the lgtmaybe version alongside the model in posts ([#284](https://github.com/MattJColes/lgtmaybe/issues/284)) ([a295cd8](https://github.com/MattJColes/lgtmaybe/commit/a295cd8309acc5c093d07e7789d3d7ff8547d375))
* **static-analysis:** add mypy to the fusion runner ([#287](https://github.com/MattJColes/lgtmaybe/issues/287)) ([b8f8739](https://github.com/MattJColes/lgtmaybe/commit/b8f8739ebcb061aea096347c5c33d7ab6a00b644))
* **static-analysis:** deterministic scan tools that replace LLM work ([#288](https://github.com/MattJColes/lgtmaybe/issues/288)) ([4eee72c](https://github.com/MattJColes/lgtmaybe/commit/4eee72cc25a312b8f00e136fac54a1fdd263ad77))
* **static-analysis:** semgrep rule pack, ast-grep, osv-scanner, and prompt narrowing ([#289](https://github.com/MattJColes/lgtmaybe/issues/289)) ([1cddf6b](https://github.com/MattJColes/lgtmaybe/commit/1cddf6b6acc28c978154e1408bfd40b3dc886771))


### Bug Fixes

* **prompt:** bind suggestion names to the file being changed ([#286](https://github.com/MattJColes/lgtmaybe/issues/286)) ([7bb2f02](https://github.com/MattJColes/lgtmaybe/commit/7bb2f02ae3df13437b1ceb20a3c59bb8a7310888))

## [1.7.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.6.0...lgtmaybe-v1.7.0) (2026-07-26)


### Features

* **diagnostics:** log the effective timeout's source; stop retrying a refusal ([#271](https://github.com/MattJColes/lgtmaybe/issues/271)) ([ee1e42c](https://github.com/MattJColes/lgtmaybe/commit/ee1e42c850a79e02b670ef1e7a2519b045fc0ee2))
* **provider:** cap generated tokens and fail fast when credit runs out ([#281](https://github.com/MattJColes/lgtmaybe/issues/281)) ([ea84ba4](https://github.com/MattJColes/lgtmaybe/commit/ea84ba4f4a49dcf2dfee1254dd350dc91bddaa42))
* **summary:** stop claiming LGTM while business is still outstanding ([#273](https://github.com/MattJColes/lgtmaybe/issues/273)) ([1b50d93](https://github.com/MattJColes/lgtmaybe/commit/1b50d93530a370b7f0b81469b60b0f48dad5eaae))


### Bug Fixes

* **dedupe:** key re-run dedupe on a prose-free finding identity ([#275](https://github.com/MattJColes/lgtmaybe/issues/275)) ([7ee4eed](https://github.com/MattJColes/lgtmaybe/commit/7ee4eedc7f435648e38d48435b80c045be417ce1))
* **engine:** report a split piece that failed instead of claiming the batch was reviewed ([#279](https://github.com/MattJColes/lgtmaybe/issues/279)) ([8885bbd](https://github.com/MattJColes/lgtmaybe/commit/8885bbdd372485a14e7e48872ca25c374e1ddbd3))
* **posting:** make an incomplete review visible on the PR ([#278](https://github.com/MattJColes/lgtmaybe/issues/278)) ([85f2e7f](https://github.com/MattJColes/lgtmaybe/commit/85f2e7ffd97c9e80757938c72050be88031533ec))
* **providers:** fail a blown wall clock once, and pin the documented timeouts ([#277](https://github.com/MattJColes/lgtmaybe/issues/277)) ([1af7efc](https://github.com/MattJColes/lgtmaybe/commit/1af7efce4a363531d41bd54cfd6c429e36217a5c))
* **workflows:** stop lgtmaybe cancelling its own reviews ([#276](https://github.com/MattJColes/lgtmaybe/issues/276)) ([1ece55e](https://github.com/MattJColes/lgtmaybe/commit/1ece55e414972a99c024cb05bec397b7a639e27c))


### Dependencies

* bump the python-dependencies group and migrate to cdk-nag v3 ([#280](https://github.com/MattJColes/lgtmaybe/issues/280)) ([bfd429b](https://github.com/MattJColes/lgtmaybe/commit/bfd429bfe6bf685b7d7519baff6eed5bcb65a345))

## [1.6.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.5.5...lgtmaybe-v1.6.0) (2026-07-26)


### Features

* **cache:** widen prompt caching to vertex, zai, and openrouter routes ([#266](https://github.com/MattJColes/lgtmaybe/issues/266)) ([461c34a](https://github.com/MattJColes/lgtmaybe/commit/461c34a918b0da7e191df8f0b648315e057ed363))
* **preset:** four distinct lenses on every provider ([#268](https://github.com/MattJColes/lgtmaybe/issues/268)) ([2d81f5e](https://github.com/MattJColes/lgtmaybe/commit/2d81f5ec7cdf7f87b8d2dfa8ec9e1aee8a64d57a))
* **timeouts:** raise every wall-clock budget so slow calls finish ([#264](https://github.com/MattJColes/lgtmaybe/issues/264)) ([90591f4](https://github.com/MattJColes/lgtmaybe/commit/90591f486c2e0be520459aee6604cd5bd161a477))


### Performance Improvements

* overlap the three serial I/O stages, isolate resolve failures ([#270](https://github.com/MattJColes/lgtmaybe/issues/270)) ([f03c4bf](https://github.com/MattJColes/lgtmaybe/commit/f03c4bf6e0e679bd3a7dbb865ba3f9a5baf8f9ea))


### Documentation

* **preset:** fix the mangled --preset help and sweep the stale call counts ([#269](https://github.com/MattJColes/lgtmaybe/issues/269)) ([44a4776](https://github.com/MattJColes/lgtmaybe/commit/44a47769051e39a3a3a6b6f82b4b0cea4a94f770))

## [1.5.5](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.5.4...lgtmaybe-v1.5.5) (2026-07-25)


### Bug Fixes

* **homebrew:** trust the tap in the smoke gate instead of disabling the check ([#262](https://github.com/MattJColes/lgtmaybe/issues/262)) ([a22bb02](https://github.com/MattJColes/lgtmaybe/commit/a22bb0227bf70db03d46d2194cfb64a515d8d5b4))

## [1.5.4](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.5.3...lgtmaybe-v1.5.4) (2026-07-25)


### Bug Fixes

* **providers:** make the wall-clock timeout clock-authoritative ([#260](https://github.com/MattJColes/lgtmaybe/issues/260)) ([979e0e5](https://github.com/MattJColes/lgtmaybe/commit/979e0e5f3a669f62920964d4401239acad8cc5e6))

## [1.5.3](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.5.2...lgtmaybe-v1.5.3) (2026-07-25)


### Bug Fixes

* load broker from flattened Lambda bundle ([#249](https://github.com/MattJColes/lgtmaybe/issues/249)) ([a886683](https://github.com/MattJColes/lgtmaybe/commit/a886683430fc3369499101bbe090ae21dd761ab8))

## [1.5.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.5.1...lgtmaybe-v1.5.2) (2026-07-25)


### Bug Fixes

* avoid deprecated GitHub App input ([#246](https://github.com/MattJColes/lgtmaybe/issues/246)) ([f97bb01](https://github.com/MattJColes/lgtmaybe/commit/f97bb01d83230717df9da95ce938319983331b84))
* skip Winget update before initial publication ([#248](https://github.com/MattJColes/lgtmaybe/issues/248)) ([72c59fa](https://github.com/MattJColes/lgtmaybe/commit/72c59fa9b1f47e6a1299428be05f85da5af5af25))

## [1.5.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.5.0...lgtmaybe-v1.5.1) (2026-07-25)


### Bug Fixes

* pass GitHub identity timeout by keyword ([#243](https://github.com/MattJColes/lgtmaybe/issues/243)) ([6d9beea](https://github.com/MattJColes/lgtmaybe/commit/6d9beea88fea3bd13428edae70059b757252f477))

## [1.5.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.4.1...lgtmaybe-v1.5.0) (2026-07-25)


### Features

* add public lgtmaybe bot identity ([#239](https://github.com/MattJColes/lgtmaybe/issues/239)) ([8ff8720](https://github.com/MattJColes/lgtmaybe/commit/8ff8720083cc678d8593e3e2424d9080565742a4))
* gate defect findings on failure scenarios ([#238](https://github.com/MattJColes/lgtmaybe/issues/238)) ([ea23a60](https://github.com/MattJColes/lgtmaybe/commit/ea23a609b23266afbc0cf53845f533e3652b9fec))


### Bug Fixes

* harden auxiliary model outputs ([#241](https://github.com/MattJColes/lgtmaybe/issues/241)) ([421c188](https://github.com/MattJColes/lgtmaybe/commit/421c1889c8d8b045dad8206758312a2288264d06))
* prevent bot comments cancelling reviews ([#240](https://github.com/MattJColes/lgtmaybe/issues/240)) ([0eeeee3](https://github.com/MattJColes/lgtmaybe/commit/0eeeee3ef7495c3199bce785fd674fb9743eaaa7))
* upgrade GitHub App token action to v3 ([#237](https://github.com/MattJColes/lgtmaybe/issues/237)) ([8b38186](https://github.com/MattJColes/lgtmaybe/commit/8b38186272590679472759afabe894e2fd13552f))


### Documentation

* expand WinGet installation guidance ([#235](https://github.com/MattJColes/lgtmaybe/issues/235)) ([777163e](https://github.com/MattJColes/lgtmaybe/commit/777163e66ccf98a8d6f09edb18a95059999c97f4))

## [1.4.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.4.0...lgtmaybe-v1.4.1) (2026-07-25)


### Bug Fixes

* generate readable Mermaid flowcharts ([#231](https://github.com/MattJColes/lgtmaybe/issues/231)) ([e85b491](https://github.com/MattJColes/lgtmaybe/commit/e85b4910621005ead0f7e70bd449f421a231d0ab))
* pass repository to release commands ([#232](https://github.com/MattJColes/lgtmaybe/issues/232)) ([1e4c633](https://github.com/MattJColes/lgtmaybe/commit/1e4c633cd9a1e209df484a8cf8833eabd6a3678d))

## [1.4.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.3.0...lgtmaybe-v1.4.0) (2026-07-25)


### Features

* add Windows executable and winget distribution ([cac1339](https://github.com/MattJColes/lgtmaybe/commit/cac1339bf29be2bd595002b8b57cbe277e95bebe))


### Bug Fixes

* harden Windows release workflows ([ee08c9f](https://github.com/MattJColes/lgtmaybe/commit/ee08c9f65333f1da5c73cc2623b16326caca2175))
* support Windows compatibility ([ab7185e](https://github.com/MattJColes/lgtmaybe/commit/ab7185e65431a5f53621168a4b1a89147866af90))


### Documentation

* simplify Marketplace Action setup ([2b9c274](https://github.com/MattJColes/lgtmaybe/commit/2b9c2741a4879befbbed6047e36e925b3d50e685))

## [1.3.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.2.0...lgtmaybe-v1.3.0) (2026-07-25)


### Features

* **diagram:** green-border changed elements; recommend App identity; marketplace assets ([#220](https://github.com/MattJColes/lgtmaybe/issues/220)) ([17700fc](https://github.com/MattJColes/lgtmaybe/commit/17700fc9ba81acd732f9bd55b80d34c8b90ace14))
* **diagram:** light-green relationship lines and labels in C4 diagrams ([#224](https://github.com/MattJColes/lgtmaybe/issues/224)) ([a1c40f5](https://github.com/MattJColes/lgtmaybe/commit/a1c40f5726a7c9439cf2738f17c39c5ea61bcd8b))
* full-screen link and density-aware layout for change diagrams ([4667dc2](https://github.com/MattJColes/lgtmaybe/commit/4667dc20b9212f221cd70dc3c504f4061978d5a0))
* generous timeout defaults for slow providers and auto diagrams by default ([18707a5](https://github.com/MattJColes/lgtmaybe/commit/18707a5b51476a5634d19a06a109b23adddbfeab))
* raise the direct-cloud timeout default to 300s ([e656b32](https://github.com/MattJColes/lgtmaybe/commit/e656b329600066ad152ba3624f842e44717baca7))


### Bug Fixes

* **diagram:** loosen C4 layout and brighten rel styling for dark mode ([#221](https://github.com/MattJColes/lgtmaybe/issues/221)) ([00116a6](https://github.com/MattJColes/lgtmaybe/commit/00116a6c20f6962e31f0be1c6fff126fef077d96))


### Documentation

* link the Marketplace listing and feature the branded screenshots ([#223](https://github.com/MattJColes/lgtmaybe/issues/223)) ([ddb39ec](https://github.com/MattJColes/lgtmaybe/commit/ddb39ecd4d1228f104d3351538f29ec0b09d4e74))

## [1.2.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.1.0...lgtmaybe-v1.2.0) (2026-07-25)


### Features

* answer author replies in review finding threads ([#217](https://github.com/MattJColes/lgtmaybe/issues/217)) ([3ecf858](https://github.com/MattJColes/lgtmaybe/commit/3ecf85860b1a6c79217f50bdc70526d30633ea6d))
* gate merges on findings via a GitHub Check Run ([#214](https://github.com/MattJColes/lgtmaybe/issues/214)) ([4cc2604](https://github.com/MattJColes/lgtmaybe/commit/4cc2604754984cab990bbb3841f818be1fff7c27))
* parallelise correctness review ([#210](https://github.com/MattJColes/lgtmaybe/issues/210)) ([f63e0ec](https://github.com/MattJColes/lgtmaybe/commit/f63e0ec8e9dc62831671051fb7f79e2fd71962c1))
* suppress findings downvoted with thumbs-down reactions ([#213](https://github.com/MattJColes/lgtmaybe/issues/213)) ([fbbebce](https://github.com/MattJColes/lgtmaybe/commit/fbbebced80e16b97169b9dcda11f2004f9784578))
* write reviews in a configurable language ([#215](https://github.com/MattJColes/lgtmaybe/issues/215)) ([23abe0a](https://github.com/MattJColes/lgtmaybe/commit/23abe0a45d3c8e0f0e8c9db9da93a2c18ea3593f))


### Bug Fixes

* **diagram:** keep C4 relationship lines readable in GitHub dark mode ([#216](https://github.com/MattJColes/lgtmaybe/issues/216)) ([317ce54](https://github.com/MattJColes/lgtmaybe/commit/317ce54f5a3b637a5c5927a3a2cdfcfd2c4ee8dc))
* harden review pipeline against watermark, expansion, and redaction bugs ([#212](https://github.com/MattJColes/lgtmaybe/issues/212)) ([f03e076](https://github.com/MattJColes/lgtmaybe/commit/f03e0761a7ec49a96bbda61b644423cbf7d51fe8))


### Documentation

* move docs site to lgtmaybe.coles.codes ([#218](https://github.com/MattJColes/lgtmaybe/issues/218)) ([65960fc](https://github.com/MattJColes/lgtmaybe/commit/65960fc2a6b5160036047c67feae3f5d44d4207b))

## [1.1.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v1.0.0...lgtmaybe-v1.1.0) (2026-07-24)


### Features

* **diff:** skip generated llms.txt/llms-full.txt files in review ([#202](https://github.com/MattJColes/lgtmaybe/issues/202)) ([b06086c](https://github.com/MattJColes/lgtmaybe/commit/b06086c23279bfd256b9e7de1ab3500f11c45474))
* enable diagrams in starter workflows ([#204](https://github.com/MattJColes/lgtmaybe/issues/204)) ([157c47d](https://github.com/MattJColes/lgtmaybe/commit/157c47dfdca040529b08bc48591be7fbdfe00ff5))


### Bug Fixes

* align action with v1 release ([#209](https://github.com/MattJColes/lgtmaybe/issues/209)) ([46e94b5](https://github.com/MattJColes/lgtmaybe/commit/46e94b518d46a31c969ba5e0623c84e32bb593d1))
* bound default review runtime ([#208](https://github.com/MattJColes/lgtmaybe/issues/208)) ([d21e4e8](https://github.com/MattJColes/lgtmaybe/commit/d21e4e8f262b809fbf75bbd4469178aaa8312519))


### Documentation

* add Google Search Console verification file ([#201](https://github.com/MattJColes/lgtmaybe/issues/201)) ([d20b6d3](https://github.com/MattJColes/lgtmaybe/commit/d20b6d31b308dd924e8b2f117e826f2f0ed94d46))
* clarify Marketplace provider setup ([#206](https://github.com/MattJColes/lgtmaybe/issues/206)) ([089e74f](https://github.com/MattJColes/lgtmaybe/commit/089e74ff209e4043f1b7c394b191eccbb190dcdf))
* improve homepage feature showcase ([#207](https://github.com/MattJColes/lgtmaybe/issues/207)) ([ab9984f](https://github.com/MattJColes/lgtmaybe/commit/ab9984f9ae3f4651425f9d769f3717f8e6716cb4))
* show change diagram on homepage ([#205](https://github.com/MattJColes/lgtmaybe/issues/205)) ([64f58f7](https://github.com/MattJColes/lgtmaybe/commit/64f58f7d5fdebdf2ff7b9e6ab8096d38128a7064))

## [1.0.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.13.1...lgtmaybe-v1.0.0) (2026-07-24)


### Features

* **action:** post reviews as a GitHub App via app_id/app_private_key inputs ([#197](https://github.com/MattJColes/lgtmaybe/issues/197)) ([88ec1e4](https://github.com/MattJColes/lgtmaybe/commit/88ec1e4e03284a28c2e8e97ccd00f98447178a41))


### Documentation

* optimise the docs site for SEO and LLM crawlers ([#200](https://github.com/MattJColes/lgtmaybe/issues/200)) ([d46078f](https://github.com/MattJColes/lgtmaybe/commit/d46078f18ebd97eedfa33b6f01e0d45f4eed32f2))

## [0.13.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.13.0...lgtmaybe-v0.13.1) (2026-07-24)


### Documentation

* accuracy pass, Mermaid diagrams, and a light-stroke header logo ([#194](https://github.com/MattJColes/lgtmaybe/issues/194)) ([394ea3a](https://github.com/MattJColes/lgtmaybe/commit/394ea3ac65bac035b4a751acb52c4c6a075bfa07))

## [0.13.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.12.2...lgtmaybe-v0.13.0) (2026-07-24)


### Features

* C4-style change diagram (/diagram, auto_diagram, lgtmaybe diagram) ([#191](https://github.com/MattJColes/lgtmaybe/issues/191)) ([4d99649](https://github.com/MattJColes/lgtmaybe/commit/4d996493d73d38a9be5dfb79f849c2177e3928c7))

## [0.12.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.12.1...lgtmaybe-v0.12.2) (2026-07-22)


### Bug Fixes

* **homebrew:** prefer wheels so litellm &gt;=1.92 sdist never builds in the sandbox ([#189](https://github.com/MattJColes/lgtmaybe/issues/189)) ([df6e7e1](https://github.com/MattJColes/lgtmaybe/commit/df6e7e1cc417bce46f8cb0823315b7af31df37b8))


### Dependencies

* bump the python-dependencies group with 6 updates ([#187](https://github.com/MattJColes/lgtmaybe/issues/187)) ([5615a79](https://github.com/MattJColes/lgtmaybe/commit/5615a792c64c760e88cb230aef73aac4b5335197))

## [0.12.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.12.0...lgtmaybe-v0.12.1) (2026-07-17)


### Performance Improvements

* cut redundant work in the pipeline and trim prompt token waste ([#185](https://github.com/MattJColes/lgtmaybe/issues/185)) ([c2ebd97](https://github.com/MattJColes/lgtmaybe/commit/c2ebd970301cfff63335c1ac4be0d39156d06084))

## [0.12.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.11.0...lgtmaybe-v0.12.0) (2026-07-17)


### Features

* add openspec living specs anchored to code with ast-grep ([#178](https://github.com/MattJColes/lgtmaybe/issues/178)) ([b61813f](https://github.com/MattJColes/lgtmaybe/commit/b61813fd274240b54d36460a3642422d38c0a2cf))


### Bug Fixes

* **deps:** cap litellm below 1.92 on Python 3.14 ([#181](https://github.com/MattJColes/lgtmaybe/issues/181)) ([0add942](https://github.com/MattJColes/lgtmaybe/commit/0add942b9a7accbc129b88208df97dfdbc59e50a))


### Dependencies

* bump the python-dependencies group with 5 updates ([#180](https://github.com/MattJColes/lgtmaybe/issues/180)) ([33d8ba0](https://github.com/MattJColes/lgtmaybe/commit/33d8ba0495bbc13d98cb730a9d3857a1dde04687))


### Documentation

* make the docs read more human and easier ([#183](https://github.com/MattJColes/lgtmaybe/issues/183)) ([137fb4a](https://github.com/MattJColes/lgtmaybe/commit/137fb4a49ab325819b6cac4be6f3af59119835ce))
* product spec for simplifying end-user CLI configuration ([#182](https://github.com/MattJColes/lgtmaybe/issues/182)) ([25f6979](https://github.com/MattJColes/lgtmaybe/commit/25f6979e0a25c8429d0b3b2aa771eedaf4b7b088))

## [0.11.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.10.0...lgtmaybe-v0.11.0) (2026-07-06)


### Features

* **cli:** add help command with usage examples ([#177](https://github.com/MattJColes/lgtmaybe/issues/177)) ([beac277](https://github.com/MattJColes/lgtmaybe/commit/beac277149225a0f9170109bf7e494bed0635d50))


### Bug Fixes

* **evals:** disable the review deadline in eval runs + one-command preset A/B ([#175](https://github.com/MattJColes/lgtmaybe/issues/175)) ([934b309](https://github.com/MattJColes/lgtmaybe/commit/934b309ed338fa3ddea152c19299bdffbe1707bf))

## [0.10.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.9.2...lgtmaybe-v0.10.0) (2026-07-06)


### ⚠ BREAKING CHANGES

* **engine:** reviews now run the fast preset by default — all nine lenses covered in four grouped model calls (~half the calls and wall time), trading some recall on the softer lenses (performance, complexity, ponytail, deprecation, tests, documentation). Set preset: full (or --preset full / the Action's preset input) to restore the previous one-call-per-lens behaviour.

### Features

* **engine:** first-class structured describe + opt-in auto-describe ([#171](https://github.com/MattJColes/lgtmaybe/issues/171)) ([35f1b08](https://github.com/MattJColes/lgtmaybe/commit/35f1b0872bada212232a69dd32e85d44e7b5de20))
* **engine:** function-boundary context, per-tool lint floors, eval A/B coverage ([#173](https://github.com/MattJColes/lgtmaybe/issues/173)) ([684acf1](https://github.com/MattJColes/lgtmaybe/commit/684acf1e2307c926a743f3a2ca53a4031011744b))
* **engine:** review-effort/risk labels + declarative finding rules ([#172](https://github.com/MattJColes/lgtmaybe/issues/172)) ([1414322](https://github.com/MattJColes/lgtmaybe/commit/14143221e8cec7037617902e154fa96d04e77769))
* **engine:** static-analysis fusion — deterministic linters ground the review ([#169](https://github.com/MattJColes/lgtmaybe/issues/169)) ([48d6ecf](https://github.com/MattJColes/lgtmaybe/commit/48d6ecff8bd5195752ad0b9267ee25db8d6403a7))
* **engine:** two-stage triage routing behind a security floor ([#170](https://github.com/MattJColes/lgtmaybe/issues/170)) ([00966fe](https://github.com/MattJColes/lgtmaybe/commit/00966feb9052dffcd6091303bbd91693cd424d71))
* **github:** commit-scoped incremental review on synchronize pushes ([#168](https://github.com/MattJColes/lgtmaybe/issues/168)) ([f16f915](https://github.com/MattJColes/lgtmaybe/commit/f16f91579d6aac9dbe8b4556a133a3126b8210c0))
* prompt caching + audit-driven review improvements ([#166](https://github.com/MattJColes/lgtmaybe/issues/166)) ([95acc2b](https://github.com/MattJColes/lgtmaybe/commit/95acc2b63aaa8062acaca603023492ad75bec90c))


### Performance Improvements

* **engine:** cut review wall time — global fan-out pool, cached diff prefix, fast preset, deadlines ([#174](https://github.com/MattJColes/lgtmaybe/issues/174)) ([853917a](https://github.com/MattJColes/lgtmaybe/commit/853917a7c45c4450e379ede033cfd04ddfdf490b))


### Dependencies

* bump the python-dependencies group across 1 directory with 3 updates ([#164](https://github.com/MattJColes/lgtmaybe/issues/164)) ([bb83a4f](https://github.com/MattJColes/lgtmaybe/commit/bb83a4f2973791d3cdb5c04ee5a87431e7b7ef9f))

## [0.9.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.9.1...lgtmaybe-v0.9.2) (2026-07-01)


### Bug Fixes

* **provider:** fail fast on expired cloud credentials ([#162](https://github.com/MattJColes/lgtmaybe/issues/162)) ([c56fa7d](https://github.com/MattJColes/lgtmaybe/commit/c56fa7d39a8468127b55c9288926befa9d5c9eb8))

## [0.9.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.9.0...lgtmaybe-v0.9.1) (2026-07-01)


### Documentation

* streamline install + local-model guides ([#160](https://github.com/MattJColes/lgtmaybe/issues/160)) ([6425ad3](https://github.com/MattJColes/lgtmaybe/commit/6425ad30e2adc866fb5001d4a8a0c3ae791ecea4))

## [0.9.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.8.2...lgtmaybe-v0.9.0) (2026-06-30)


### Features

* **homebrew:** install lgtmaybe + deps from PyPI wheels (preserve_rpath) ([#157](https://github.com/MattJColes/lgtmaybe/issues/157)) ([fe10d12](https://github.com/MattJColes/lgtmaybe/commit/fe10d12810a37e057f743a0bbbb0699c6fc87173))

## [0.8.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.8.1...lgtmaybe-v0.8.2) (2026-06-30)


### Documentation

* **homebrew:** document the required `brew trust` step for the tap ([#155](https://github.com/MattJColes/lgtmaybe/issues/155)) ([5099cd3](https://github.com/MattJColes/lgtmaybe/commit/5099cd324247303263c6293e201a7c6a6e21a7dc))

## [0.8.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.8.0...lgtmaybe-v0.8.1) (2026-06-30)


### Bug Fixes

* **homebrew:** make the tap actually publish (path, tap registration, first-publish commit) ([#154](https://github.com/MattJColes/lgtmaybe/issues/154)) ([4e71803](https://github.com/MattJColes/lgtmaybe/commit/4e718030197ec091367bc2fbec8e738b623818b6))
* **homebrew:** make the tap publish reliably on release ([#152](https://github.com/MattJColes/lgtmaybe/issues/152)) ([b46619b](https://github.com/MattJColes/lgtmaybe/commit/b46619bc42a2e48d973b79d7977d0ad1f5a10dac))

## [0.8.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.7.2...lgtmaybe-v0.8.0) (2026-06-30)


### Features

* add Homebrew install support ([#143](https://github.com/MattJColes/lgtmaybe/issues/143)) ([e4b9e1b](https://github.com/MattJColes/lgtmaybe/commit/e4b9e1b357e4edc3c40b0635f8926cc98edbcf7e))
* resolve cross-file symbols with ast-grep during reflection ([#144](https://github.com/MattJColes/lgtmaybe/issues/144)) ([0ad9cdf](https://github.com/MattJColes/lgtmaybe/commit/0ad9cdf91a2f05fab716beb469a63e2ca4093779))


### Bug Fixes

* surface clear errors for malformed payloads and tiny reflect budgets ([#148](https://github.com/MattJColes/lgtmaybe/issues/148)) ([dad6e7a](https://github.com/MattJColes/lgtmaybe/commit/dad6e7a61300a192104f376c9b1eafb4f457c0da))


### Documentation

* align end-user docs with the codebase ([#146](https://github.com/MattJColes/lgtmaybe/issues/146)) ([fd7bf94](https://github.com/MattJColes/lgtmaybe/commit/fd7bf94e3ea9780f699dbfae8f31f42a179e8e25))
* optimise documentation for SEO and LLM crawlers ([#147](https://github.com/MattJColes/lgtmaybe/issues/147)) ([b881877](https://github.com/MattJColes/lgtmaybe/commit/b881877b3192964c71a9866e7070ecf3ceb5fed6))

## [0.7.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.7.1...lgtmaybe-v0.7.2) (2026-06-30)


### Documentation

* add how-to contents, default examples to Sonnet, and support Python 3.11 ([#141](https://github.com/MattJColes/lgtmaybe/issues/141)) ([a79bb12](https://github.com/MattJColes/lgtmaybe/commit/a79bb122a5dbcfc4bdad02dde131138cbec7dbc0))

## [0.7.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.7.0...lgtmaybe-v0.7.1) (2026-06-30)


### Dependencies

* bump the python-dependencies group with 5 updates ([#139](https://github.com/MattJColes/lgtmaybe/issues/139)) ([8b03a49](https://github.com/MattJColes/lgtmaybe/commit/8b03a493b2ee9fa73e12ddd83840f57e3f3d3025))

## [0.7.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.6.2...lgtmaybe-v0.7.0) (2026-06-24)


### Features

* cut false positives from real-project feedback (prompts, grounded reflection, retrieval, benchmark) ([#136](https://github.com/MattJColes/lgtmaybe/issues/136)) ([e98eddf](https://github.com/MattJColes/lgtmaybe/commit/e98eddf56c98b0e90129cd3fd3b5dc7579c8f954))

## [0.6.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.6.1...lgtmaybe-v0.6.2) (2026-06-23)


### Documentation

* add per-provider how-to guides and reframe the OpenAI-compatible guide ([#133](https://github.com/MattJColes/lgtmaybe/issues/133)) ([14c5470](https://github.com/MattJColes/lgtmaybe/commit/14c5470c56e53bfdf9b1ae279641a98d397bb30a))

## [0.6.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.6.0...lgtmaybe-v0.6.1) (2026-06-23)


### Bug Fixes

* **diffparse:** ignore "No newline at end of file" markers when anchoring ([#128](https://github.com/MattJColes/lgtmaybe/issues/128)) ([94a03e3](https://github.com/MattJColes/lgtmaybe/commit/94a03e3a8f44172a3d72197eb6f5e474d7a97c05))
* **github:** defang triple-backticks in finding title and body ([#132](https://github.com/MattJColes/lgtmaybe/issues/132)) ([0496939](https://github.com/MattJColes/lgtmaybe/commit/0496939f530044699f752a5c98c4f9bed2effe73))
* **models:** coerce model severity case so mixed-case findings aren't dropped ([#131](https://github.com/MattJColes/lgtmaybe/issues/131)) ([82c9078](https://github.com/MattJColes/lgtmaybe/commit/82c9078b02b625de36ccc5c575863beea02234a3))
* **redact:** bound scheme length to stop quadratic connection-string match ([#130](https://github.com/MattJColes/lgtmaybe/issues/130)) ([4701287](https://github.com/MattJColes/lgtmaybe/commit/4701287f518acc8635aac6e296dfd0199d97ed17))


### Documentation

* correct stale defaults, dedupe key, and release/redaction descriptions ([#127](https://github.com/MattJColes/lgtmaybe/issues/127)) ([266137f](https://github.com/MattJColes/lgtmaybe/commit/266137fe4819b4c18f4e744cf906462ef40b14aa))

## [0.6.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.5.0...lgtmaybe-v0.6.0) (2026-06-23)


### Features

* **providers:** add first-class zai (GLM / Zhipu AI) provider ([#125](https://github.com/MattJColes/lgtmaybe/issues/125)) ([f103e4f](https://github.com/MattJColes/lgtmaybe/commit/f103e4f1571173dae90eeec201de4d1211f35b30))

## [0.5.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.4.1...lgtmaybe-v0.5.0) (2026-06-23)


### Features

* **engine:** collapse same-location findings regardless of title wording ([#123](https://github.com/MattJColes/lgtmaybe/issues/123)) ([bfd2031](https://github.com/MattJColes/lgtmaybe/commit/bfd2031fe0c15104381c12bec97ca4cd2e14c3b6))

## [0.4.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.4.0...lgtmaybe-v0.4.1) (2026-06-22)


### Bug Fixes

* **evals:** resolve credentials so the harness runs keyless local endpoints ([#121](https://github.com/MattJColes/lgtmaybe/issues/121)) ([cfd840a](https://github.com/MattJColes/lgtmaybe/commit/cfd840ad48f1bc57abdcbd22f24721d0fcf2b719))
* keep parsing past a non-finding JSON object in model output ([#119](https://github.com/MattJColes/lgtmaybe/issues/119)) ([8296fde](https://github.com/MattJColes/lgtmaybe/commit/8296fdee73aef94c96915d55fe1814ca222ce442))
* recover empty structured-output responses from local gateways ([#118](https://github.com/MattJColes/lgtmaybe/issues/118)) ([5b70a04](https://github.com/MattJColes/lgtmaybe/commit/5b70a048a56e02e0f24a5f84583e2e71fbfc8aa6))


### Documentation

* correct the "review incomplete" error string in the ollama guide ([#120](https://github.com/MattJColes/lgtmaybe/issues/120)) ([1ae700b](https://github.com/MattJColes/lgtmaybe/commit/1ae700b7ca6af611271d81ee4b7e05a8027b8ba5))

## [0.4.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.3.3...lgtmaybe-v0.4.0) (2026-06-22)


### Features

* cut weak-model false positives with confidence gates ([#115](https://github.com/MattJColes/lgtmaybe/issues/115)) ([1911edc](https://github.com/MattJColes/lgtmaybe/commit/1911edc02c5ce1a32ad8bdbced3c6b2e975b0ad6))


### Bug Fixes

* **e2e:** use llama.cpp -hf quant tag instead of filename ([#117](https://github.com/MattJColes/lgtmaybe/issues/117)) ([c5b279f](https://github.com/MattJColes/lgtmaybe/commit/c5b279f451c3f9af994f8afe20fe4b5fe76a3f0a))


### Dependencies

* bump the python-dependencies group with 5 updates ([#114](https://github.com/MattJColes/lgtmaybe/issues/114)) ([a81be06](https://github.com/MattJColes/lgtmaybe/commit/a81be069d074a928ca2af126773975e6669590ed))

## [0.3.3](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.3.2...lgtmaybe-v0.3.3) (2026-06-19)


### Documentation

* correct stale claims across the docs tree ([#112](https://github.com/MattJColes/lgtmaybe/issues/112)) ([cbc0bba](https://github.com/MattJColes/lgtmaybe/commit/cbc0bba2b111765dd6a7520b1014f6a0825c209b))
* document codebase humility and eval false-positive scoring ([#109](https://github.com/MattJColes/lgtmaybe/issues/109)) ([ad42254](https://github.com/MattJColes/lgtmaybe/commit/ad422544116ea9abcff30ad9a83b9b219619d331))

## [0.3.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.3.1...lgtmaybe-v0.3.2) (2026-06-19)


### Bug Fixes

* robustly parse findings JSON from gateways without JSON mode ([#105](https://github.com/MattJColes/lgtmaybe/issues/105)) ([f84fd77](https://github.com/MattJColes/lgtmaybe/commit/f84fd774e4602bd0425526e56f031983774756e1))

## [0.3.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.3.0...lgtmaybe-v0.3.1) (2026-06-17)


### Bug Fixes

* reframe demoted-findings body section for customers ([#102](https://github.com/MattJColes/lgtmaybe/issues/102)) ([f296911](https://github.com/MattJColes/lgtmaybe/commit/f296911de5a91b54a04fc0174dbc08aeed20daa8))

## [0.3.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.2.1...lgtmaybe-v0.3.0) (2026-06-17)


### Features

* deterministically anchor findings to the lines they describe ([#100](https://github.com/MattJColes/lgtmaybe/issues/100)) ([0cee324](https://github.com/MattJColes/lgtmaybe/commit/0cee324b67bf1672a411dd76f31a8035a7ba2cc9))

## [0.2.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.2.0...lgtmaybe-v0.2.1) (2026-06-16)


### Bug Fixes

* anchor inline comments by line+side, not deprecated position ([#97](https://github.com/MattJColes/lgtmaybe/issues/97)) ([621a030](https://github.com/MattJColes/lgtmaybe/commit/621a030c0890ccef170242a781f353e8aaa2e199))

## [0.2.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.11...lgtmaybe-v0.2.0) (2026-06-16)


### Features

* add curated opt-in lens packs and inspired-by attribution ([#91](https://github.com/MattJColes/lgtmaybe/issues/91)) ([b75e82d](https://github.com/MattJColes/lgtmaybe/commit/b75e82d04128e7b24b686c54c8f7d31f85b881e9))
* recursive (RLM) hunk-walk by default, with opt-out and an A/B benchmark ([#95](https://github.com/MattJColes/lgtmaybe/issues/95)) ([d855190](https://github.com/MattJColes/lgtmaybe/commit/d8551906bd3210b7164f06ca4d135054aa831dbe))

## [0.1.11](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.10...lgtmaybe-v0.1.11) (2026-06-16)


### Bug Fixes

* fail fast on permanent provider errors and bound the lens fan-out ([#92](https://github.com/MattJColes/lgtmaybe/issues/92)) ([0da9be0](https://github.com/MattJColes/lgtmaybe/commit/0da9be063f6fc4a49662b60c34490f5b4f8b00c8))

## [0.1.10](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.9...lgtmaybe-v0.1.10) (2026-06-15)


### Features

* **engine:** log per-lens progress so a running Action shows it's not stuck ([#87](https://github.com/MattJColes/lgtmaybe/issues/87)) ([8f821e2](https://github.com/MattJColes/lgtmaybe/commit/8f821e20a1a93ae73b92fb3b24e69ce856fe2cda))

## [0.1.9](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.8...lgtmaybe-v0.1.9) (2026-06-15)


### Bug Fixes

* make the model emit code in `suggestion`, prose in `body` ([#83](https://github.com/MattJColes/lgtmaybe/issues/83)) ([f2c5697](https://github.com/MattJColes/lgtmaybe/commit/f2c5697a5e2b93c91c31399048a1ec36847d74a9))

## [0.1.8](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.7...lgtmaybe-v0.1.8) (2026-06-15)


### Bug Fixes

* **deps:** bump aiohttp to 3.14.1 for CVE fixes ([#86](https://github.com/MattJColes/lgtmaybe/issues/86)) ([542525e](https://github.com/MattJColes/lgtmaybe/commit/542525e7e91bb9fbbac1b7127232e0bb9d219d4f))
* retry without temperature when a model rejects the value ([#84](https://github.com/MattJColes/lgtmaybe/issues/84)) ([a79e91c](https://github.com/MattJColes/lgtmaybe/commit/a79e91cb5dd20e5e3e08acdfc7efec5e4ee88220))

## [0.1.7](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.6...lgtmaybe-v0.1.7) (2026-06-15)


### Features

* add built-in ponytail lens (lazy senior dev / write less code) ([666eb3e](https://github.com/MattJColes/lgtmaybe/commit/666eb3ef234efe6877fe69566d1d66f98bcf116f))
* add openai-compatible provider for custom OpenAI /v1 endpoints ([a83c345](https://github.com/MattJColes/lgtmaybe/commit/a83c345be9792c0a30f5b44aa35142aa8893b90f))
* **evals:** add RLM-style recursive hunk-walking spike ([4cb5fb7](https://github.com/MattJColes/lgtmaybe/commit/4cb5fb77cd7083c1822f2763e2d5cde0bd62ce0e))
* give openai-compatible the long local-server timeout default ([b223e01](https://github.com/MattJColes/lgtmaybe/commit/b223e016f4674d0632e6b5bdf8201408d1452079))
* support user-defined review lenses (BYO skills) ([831683b](https://github.com/MattJColes/lgtmaybe/commit/831683b993817e8f2ea62afe24074eb7af8933b0))


### Documentation

* add FOSS roadmap explainer with community open questions ([28f01c6](https://github.com/MattJColes/lgtmaybe/commit/28f01c626d2149a20354f94ad70365a06830f427))
* **ollama:** recommend Qwen3.6-27B with RAM-based sizing guidance ([ed410b3](https://github.com/MattJColes/lgtmaybe/commit/ed410b310b8762d6020b88301991bb15d06a7846))
* remove the FOSS roadmap explainer ([9964fc1](https://github.com/MattJColes/lgtmaybe/commit/9964fc1b404acf64de51961de32b4a90a291fba2))

## [0.1.6](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.5...lgtmaybe-v0.1.6) (2026-06-14)


### Dependencies

* bump the python-dependencies group with 3 updates ([5c376bf](https://github.com/MattJColes/lgtmaybe/commit/5c376bf52a8ba45d8cd61ae8aff8b4beea08bd4e))


### Documentation

* **bedrock:** require both inference-profile and foundation-model ARNs in IAM policy ([4b34f97](https://github.com/MattJColes/lgtmaybe/commit/4b34f975b76443bcad25e6381de6f1a0357b269e))

## [0.1.5](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.4...lgtmaybe-v0.1.5) (2026-06-14)


### Performance Improvements

* **e2e:** single 9B local model + cached, single-fixture ollama run ([64e49f9](https://github.com/MattJColes/lgtmaybe/commit/64e49f9a0b06549c80b220afaeb7630a66873fb3))


### Documentation

* **bedrock:** clarify valid model ids and inference profiles ([4ca94fd](https://github.com/MattJColes/lgtmaybe/commit/4ca94fd0f623710b5ff45304ed0e9994318499e0))
* **bedrock:** clarify valid model ids and inference profiles ([417e64c](https://github.com/MattJColes/lgtmaybe/commit/417e64caf65650be9a80bb5f0f0e33eb910b5477))

## [0.1.4](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.3...lgtmaybe-v0.1.4) (2026-06-14)


### Bug Fixes

* bundle boto3 + google-auth so keyless Bedrock and Vertex work ([7da1070](https://github.com/MattJColes/lgtmaybe/commit/7da1070f38ee67b1f85986ec952529e8267ecb6c))


### Documentation

* bump actions/checkout to v6 in docs and README ([0d195e0](https://github.com/MattJColes/lgtmaybe/commit/0d195e062d05529a8910d11a03eca1f165b0980c))

## [0.1.3](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.2...lgtmaybe-v0.1.3) (2026-06-14)


### Bug Fixes

* **provider:** drop model-unsupported params instead of failing the review ([ff1bf3b](https://github.com/MattJColes/lgtmaybe/commit/ff1bf3b6b8af670b5729dbb4d225d96e6aec5c91))

## [0.1.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.1...lgtmaybe-v0.1.2) (2026-06-14)


### Bug Fixes

* **e2e:** pool eval recall across fixtures so a one-finding miss can'… ([f39eb66](https://github.com/MattJColes/lgtmaybe/commit/f39eb6657297dcb64febefb45ecd4c0c03c15f37))

## [0.1.1](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.1.0...lgtmaybe-v0.1.1) (2026-06-14)


### Features

* add performance and complexity review lenses ([73d99ee](https://github.com/MattJColes/lgtmaybe/commit/73d99ee2f72210717c96da951db0445b2e14f32a))
* **cli:** add --uncommitted flag for reviewing only working-tree edits ([8ce1cc0](https://github.com/MattJColes/lgtmaybe/commit/8ce1cc0ac98dc26559e0538a433e21faa456682d))
* **e2e:** real ollama CI run on a large multi-file diff + tunable context/timeout knobs ([62733a2](https://github.com/MattJColes/lgtmaybe/commit/62733a2f78bc178ac2e746646f3e9b6ea224adc3))
* **engine:** add intent lens and broaden review-prompt coverage ([dc4b20b](https://github.com/MattJColes/lgtmaybe/commit/dc4b20b6a4353180b99e11b124f98e0ab70f6347))
* gate example review workflows to trusted authors ([ac61548](https://github.com/MattJColes/lgtmaybe/commit/ac61548196423a0212afb8c00bac8feceaf35e83))
* gate example review workflows to trusted authors ([75a3dca](https://github.com/MattJColes/lgtmaybe/commit/75a3dcaf403e50142c52d9e78ca95609132013c1))
* intent lens, broader scan prompts, and remote-primary-branch CLI comparison ([321abbc](https://github.com/MattJColes/lgtmaybe/commit/321abbc936225bf57d61bfb60501ceab794ad4c9))
* name provider+model and scope review marker per provider ([ae2dc92](https://github.com/MattJColes/lgtmaybe/commit/ae2dc92c83a7bc4140e4a32e9d981f8133e261ba))
* name provider+model and scope review marker per provider ([568d863](https://github.com/MattJColes/lgtmaybe/commit/568d863c201092901673c4349fecfce5171807d8))


### Bug Fixes

* align provider connector docs with the code (+ fix local Vertex/Bedrock cred detection) ([c480f1a](https://github.com/MattJColes/lgtmaybe/commit/c480f1a42bcbbed136251970fcedf8a7f874f2d8))
* **cli:** compare local reviews to the remote primary branch ([5b06c7b](https://github.com/MattJColes/lgtmaybe/commit/5b06c7b35588938131838a8d4a889e217b7aa66a))
* **credentials:** recognize ADC, VERTEXAI_PROJECT, and ~/.aws as ambient creds ([5f30117](https://github.com/MattJColes/lgtmaybe/commit/5f301175e3c0a43aed7ed9d5798ab62a52927c0f))
* drop PyPI job from reusable release.yml (moved inline) ([35bdf26](https://github.com/MattJColes/lgtmaybe/commit/35bdf26f9ffdc54e4f0424c7270e562c4aea27a4))
* **e2e:** use qwen3:1.7b + --no-reflect so the ollama recall bar is reachable ([28593f4](https://github.com/MattJColes/lgtmaybe/commit/28593f4bac1d638e861e1506340f33b656665722))
* **github:** follow pagination when locating the existing review ([e61b2ea](https://github.com/MattJColes/lgtmaybe/commit/e61b2ea18729f7f7c86c7ccc3a7e0b098878956b))
* **github:** paginate the existing-review lookup, plus code-quality pass fixes ([39f632f](https://github.com/MattJColes/lgtmaybe/commit/39f632f8aecce2e728ad28c6dfc5767cb731c763))
* inline PyPI publish for trusted publishing ([95cc3af](https://github.com/MattJColes/lgtmaybe/commit/95cc3af7e28aa5c0c627f7ff20b94889e7b32123))
* inline PyPI publish so trusted publishing matches release-please.yml ([da31c0f](https://github.com/MattJColes/lgtmaybe/commit/da31c0f35efb2ac1e6546c0acf02668ac1577398))
* **ollama:** raise default num_ctx to 32768 and tighten e2e recall floor ([1095d78](https://github.com/MattJColes/lgtmaybe/commit/1095d78b4dd7a00168a654b4db61c6bb62d33d18))
* **security:** broaden redaction, case-fold injection markers, escape suggestion fences ([02a0cfa](https://github.com/MattJColes/lgtmaybe/commit/02a0cfae963c1445ed962feef0365a99fcd42155))
* **security:** broaden redaction, case-fold injection markers, escape suggestion fences ([b00e2b4](https://github.com/MattJColes/lgtmaybe/commit/b00e2b4dda7ce7ad2ff5b1afdb7a2303b15ae7b0))
* surface the real provider error on a failed review ([c264111](https://github.com/MattJColes/lgtmaybe/commit/c264111e5a9c6578df2e986227f94e9c78ef75e2))
* surface the real provider error on a failed review ([6d166aa](https://github.com/MattJColes/lgtmaybe/commit/6d166aa8cda4918baae0fd2fd7077615c46a90b2))


### Performance Improvements

* avoid redundant PR re-fetch and cache prompt/tokenizer builds ([89842a7](https://github.com/MattJColes/lgtmaybe/commit/89842a7580bec4f6edf54f352de7209daaa6c827))


### Documentation

* add ARCHITECTURE.md and fix stale code comments ([2b2713e](https://github.com/MattJColes/lgtmaybe/commit/2b2713ecaa7f067744f3194d7f17d17dacf72df9))
* add Ayu-dark CLI cards and PR-comment mockups for every scan type ([72ea4be](https://github.com/MattJColes/lgtmaybe/commit/72ea4be291c6e143b1898c324ca4f1e43d29301b))
* add PR review screenshots to README and what-gets-reviewed ([af1a3f5](https://github.com/MattJColes/lgtmaybe/commit/af1a3f5c616f773948ba14c8fef904abb4e7f03b))
* add Trust and Cost explanation and README security/cost callout ([50c83c3](https://github.com/MattJColes/lgtmaybe/commit/50c83c38e356d8e51f2a7ee82877e23bf6d64742))
* add Trust and Cost explanation and README security/cost callout ([87c6a77](https://github.com/MattJColes/lgtmaybe/commit/87c6a77796d6af80f9180d98ff33640ce6fa4057))
* align provider connector docs with the code ([90e0578](https://github.com/MattJColes/lgtmaybe/commit/90e0578509374aa47349eabdd15ae78b0c6b5751))
* correct provider count to six hosted plus local ollama ([d80c6b4](https://github.com/MattJColes/lgtmaybe/commit/d80c6b41530fbebc9bfdb31d3ffac73bcaf79855))
* correct the stale num_ctx default from 16384 to 32768 ([65fa85f](https://github.com/MattJColes/lgtmaybe/commit/65fa85ffd225e52a45c52cf0d519b84b79c85164))
* reframe trust/cost docs around choosing who can trigger reviews ([cfe5fa4](https://github.com/MattJColes/lgtmaybe/commit/cfe5fa4d7d6d95e35a26daef01c94d55aca1771e))
* refresh CLAUDE.md and releasing guide for current state ([e0d8eb5](https://github.com/MattJColes/lgtmaybe/commit/e0d8eb5a39c0e66d3c2d9eff0093f6eb38a5023a))

## [0.1.0](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.0.2...lgtmaybe-v0.1.0) (2026-06-07)


### Miscellaneous Chores

* release 0.1.0 ([3e4fa01](https://github.com/MattJColes/lgtmaybe/commit/3e4fa017d3d79377a4a88123ad9a72b2278dd289))

## [0.0.2](https://github.com/MattJColes/lgtmaybe/compare/lgtmaybe-v0.0.1...lgtmaybe-v0.0.2) (2026-06-07)


### Features

* add local CLI review mode + user-level config ([f0b8035](https://github.com/MattJColes/lgtmaybe/commit/f0b803553178eb89f0ca99cef6547474f4e39f0c))
* agent output format + local AI-fix loop ([425ac2a](https://github.com/MattJColes/lgtmaybe/commit/425ac2a34b2cd26b15ae77119763f72869431220))
* agent output format + local AI-fix loop; drop CLI-posting ollama workflow ([6325033](https://github.com/MattJColes/lgtmaybe/commit/63250336ecda254d1001db5ab9febee81b73888b))
* configurable timeout, drop cost from summary, unblock small local models ([5b79c4e](https://github.com/MattJColes/lgtmaybe/commit/5b79c4e3ea2e319f7b1f47bf9e919e7b44844898))
* configurable timeout, drop cost summary, unblock small local models ([239a93a](https://github.com/MattJColes/lgtmaybe/commit/239a93a4f08f0462e9a2449c30a45f6b5ebb55fa))
* deterministic reviews (temperature=0) + skippable reflection ([d214d38](https://github.com/MattJColes/lgtmaybe/commit/d214d3874643d3a494b70787958a2df2ff4fabd9))
* deterministic reviews (temperature=0) and skippable reflection ([91d8131](https://github.com/MattJColes/lgtmaybe/commit/91d81317bb016660be61ee7c96a5370560311b96))
* eval harness — measure whether a model produces usable reviews ([e928cb0](https://github.com/MattJColes/lgtmaybe/commit/e928cb02759b24f94c0db8bdb9a871e10e1d5a06))
* eval harness — measure whether a model produces usable reviews ([3a37efd](https://github.com/MattJColes/lgtmaybe/commit/3a37efda56fdd54dfc2d6c5f44797f463003ddd5)), closes [#27](https://github.com/MattJColes/lgtmaybe/issues/27)
* expand reviewer scan coverage (logic, tests, docs) and refresh docs ([50a8eca](https://github.com/MattJColes/lgtmaybe/commit/50a8eca14ac50ed13ba8285676a9e99b85072703))
* expand reviewer scan coverage (logic, tests, docs) and refresh docs ([38ea0f9](https://github.com/MattJColes/lgtmaybe/commit/38ea0f927e8a843af7a49d8f2fb8d38800d3825a))
* expose timeout and temperature as GitHub Action inputs ([fab5282](https://github.com/MattJColes/lgtmaybe/commit/fab5282a8207c9aa92fcacff132d5a24784aa8ce))
* expose timeout and temperature as GitHub Action inputs ([1641883](https://github.com/MattJColes/lgtmaybe/commit/164188349def4b361ae85d3440f7f2830107f482))
* local CLI review mode + user-level config ([5613c5e](https://github.com/MattJColes/lgtmaybe/commit/5613c5e33a0259661e130f90a6f1c60266f9f16d))
* parallel tracks — providers, github, engine, config/CLI, docs ([8f9e2cd](https://github.com/MattJColes/lgtmaybe/commit/8f9e2cd25c5e5e2db1ed255b23ce5660a6bb8e09))
* parallel tracks — providers, github, engine, config/CLI, docs ([3baffb6](https://github.com/MattJColes/lgtmaybe/commit/3baffb66dd4db5cd1857849c54a271a01db45d0d))
* per-category fan-out; remove cost cap and approximate cost ([4a54f97](https://github.com/MattJColes/lgtmaybe/commit/4a54f97809c38a0b71848aac63a234b99175a42f))
* per-category fan-out; remove cost cap and approximate cost ([d0536d9](https://github.com/MattJColes/lgtmaybe/commit/d0536d91475a9a38cc1a5f0d7981f83854b4f2eb))
* review diff hunks with surrounding context lines ([1849198](https://github.com/MattJColes/lgtmaybe/commit/184919885cb76e8f608ed8959b6030897f11ab49))
* review diff hunks with surrounding context lines ([07adc5e](https://github.com/MattJColes/lgtmaybe/commit/07adc5e72b549c124a9597a7ae7e50e7ba9adc12))
* step 3 integration — real adapters, slash commands, guards ([4c33a19](https://github.com/MattJColes/lgtmaybe/commit/4c33a190adbd495cb3d32d7154a66b348a7cb42f))
* step 4 packaging — Action, release pipeline, examples, current models ([c60f0dc](https://github.com/MattJColes/lgtmaybe/commit/c60f0dcc099673bbc507511b1cb5407ffc907784))
* step 4 packaging — action.yml, release pipeline, examples, current models ([3a406fe](https://github.com/MattJColes/lgtmaybe/commit/3a406fe8fdfd952b19b7ddcc15d7f376a46a9926))
* structured output for the reflection pass ([c902f10](https://github.com/MattJColes/lgtmaybe/commit/c902f10d8e9c47b7af12c3bbfc14a6e7005339e5))
* structured output for the reflection pass ([4446ffb](https://github.com/MattJColes/lgtmaybe/commit/4446ffb42f7de058e36527e6af8aa92a2ecd77a6))
* structured outputs — constrain models to valid findings JSON ([39d1958](https://github.com/MattJColes/lgtmaybe/commit/39d1958ea5218a5c68797af7a299189b4933b7bd))
* structured outputs — constrain models to valid findings JSON ([413f582](https://github.com/MattJColes/lgtmaybe/commit/413f582bebdb8c11bc1a1536846dd600e84bae59)), closes [#27](https://github.com/MattJColes/lgtmaybe/issues/27)
* wire step 3 integration — real adapters, slash commands, guards ([4c13ec8](https://github.com/MattJColes/lgtmaybe/commit/4c13ec810c406046277aeb3e0b08e355dd320654))


### Bug Fixes

* reliable local reviews — provider-aware timeout/concurrency + fail loud ([d3e0ccc](https://github.com/MattJColes/lgtmaybe/commit/d3e0cccc43fe55c4cc92adfea5163f3d10c4f6c6))
* reliable local reviews — provider-aware timeout/concurrency + fail loud ([8f8474b](https://github.com/MattJColes/lgtmaybe/commit/8f8474b8966a5a9b8550cf7f4ac713b6eebf584d))
* use the factory-resolved model string at completion time ([311d31b](https://github.com/MattJColes/lgtmaybe/commit/311d31b978684d299b78172f25c45714c61c09e4))


### Dependencies

* bump litellm in the python-dependencies group ([8e105f1](https://github.com/MattJColes/lgtmaybe/commit/8e105f12c224c984e0bfa2734094311d88a92d03))


### Documentation

* add CONTRIBUTING.md ([dee443e](https://github.com/MattJColes/lgtmaybe/commit/dee443e02d22fc605382f6462704aa3b8149a4d6))
* add project logo ([c9dcdab](https://github.com/MattJColes/lgtmaybe/commit/c9dcdab9f920271a96ddd2d33a587ea33db6f2db))
* add project logo ([5368472](https://github.com/MattJColes/lgtmaybe/commit/53684728e8c7c1a5f3c81b49efcee49c02ed3548))
* add raster favicons and apple-touch-icon ([a302a32](https://github.com/MattJColes/lgtmaybe/commit/a302a32751561e144fa021fd64ff339fa9c5a859))
* drop max_cost_usd from the user-facing scope summary ([dbad25b](https://github.com/MattJColes/lgtmaybe/commit/dbad25b551e9d96fdd13a7fd771d42b6041ee934))
* explain what gets reviewed, scoping, and output shape ([b43d0b3](https://github.com/MattJColes/lgtmaybe/commit/b43d0b3789f6bb4c5e1c02fdc143304295c3efe5))
* fix CLI usage for the local-only review command ([7e37abb](https://github.com/MattJColes/lgtmaybe/commit/7e37abba2e68ec89c2185a0b9e7bff49baf5b30f))
* fold manual-steps.md into docs/, delete the file ([c69ae5c](https://github.com/MattJColes/lgtmaybe/commit/c69ae5ca2a8f215e41216cd17d290808b1bda244))
* homepage — cover local + Action review, bullet the functionality ([2fb0e58](https://github.com/MattJColes/lgtmaybe/commit/2fb0e58b369912bc4a33d5f7eb301bd87a67dd93))
* homepage copy — tagline, functionality bullets, local + Action review ([02c9e00](https://github.com/MattJColes/lgtmaybe/commit/02c9e001d35564e76c6b0a596c48825e2169c761))
* host the docs on GitHub Pages via MkDocs Material ([9e11a46](https://github.com/MattJColes/lgtmaybe/commit/9e11a46521c6d6c8d064f71df0e532b9a3d84fec))
* left-align homepage tagline ([d85597d](https://github.com/MattJColes/lgtmaybe/commit/d85597d06f7432512de62c2ba48f785219b893ef))
* make the inline/summary feature a paragraph, not a bullet ([0cddca3](https://github.com/MattJColes/lgtmaybe/commit/0cddca3150492b5067875a3ba442831427adc052))
* make the inline/summary feature a paragraph, not a bullet ([1ee8145](https://github.com/MattJColes/lgtmaybe/commit/1ee81453f07922dccaf47b4ae4eeac65dc4cad7e))
* **ollama:** replace codellama with current model recommendations ([0bbe319](https://github.com/MattJColes/lgtmaybe/commit/0bbe319beedd73dee27c8fba986f39f13020a1ce))
* record step 4 packaging in CLAUDE.md ([9db1ba7](https://github.com/MattJColes/lgtmaybe/commit/9db1ba7d60093db62585c961a9b37ba45503bd18))
* theme to logo colours, center hero, note context-line review ([0956e61](https://github.com/MattJColes/lgtmaybe/commit/0956e617bff3625513c26472479d7977815dd7bd))
* theme to logo colours, center hero, note context-line review ([b831b1e](https://github.com/MattJColes/lgtmaybe/commit/b831b1eaf9848e43fb87d03f1650ca9003e68894))
* trim manual-steps to remaining human-only actions ([8ccd3d5](https://github.com/MattJColes/lgtmaybe/commit/8ccd3d5f9e98071f012121cb789e4567cc7a4aa6))

## Changelog
