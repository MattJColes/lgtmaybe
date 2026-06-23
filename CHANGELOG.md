# Changelog

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
