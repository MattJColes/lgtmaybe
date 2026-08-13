## Why

The ports use runtime abstract inheritance even though dependency injection and static method signatures are the actual seams; production implementations and fakes do not need base-class coupling.

## What Changes

- Express all three ports as `typing.Protocol` contracts.
- Remove port inheritance from adapters, engine, and test fakes.
- Replace abstractness tests with structural conformance tests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. Port signatures remain frozen; only their runtime representation changes, so `skip_specs` is enabled.

## Impact

Core port definitions and implementing class declarations change; exceptions and public method signatures stay unchanged.
