## Context

`RuntimeOptions` is consumed only within `cli`; provider defaults are shared by factory, credentials, adapter, and tests.

## Goals / Non-Goals

**Goals:** Delete both single-symbol files while preserving their one authoritative definition.

**Non-Goals:** Change public CLI exports or provider defaults.

## Decisions

Define `RuntimeOptions` in `cli/__init__.py` beside action input handling. Define provider constants in `factory.py`; its litellm import is lazy, so credentials and the adapter can import constants without an eager litellm dependency.

## Risks / Trade-offs

- Import cycles → verify direct imports of CLI, credentials, factory, and adapter in tests.
