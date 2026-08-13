## Context

Each port has one production implementation and several injected test fakes. Runtime subclass checks are not used by production code.

## Goals / Non-Goals

**Goals:** Preserve type-checkable contracts without mandatory inheritance.

**Non-Goals:** Change port methods, exception contracts, or dependency injection.

## Decisions

Use plain `Protocol` classes and ellipsis method bodies. Remove inheritance from concrete classes to prove structural typing is sufficient. Keep the existing port names and imports so annotations remain stable.

## Risks / Trade-offs

- Runtime `issubclass`/instantiation behaviour changes → replace tests with concrete structural assignments and run mypy.
