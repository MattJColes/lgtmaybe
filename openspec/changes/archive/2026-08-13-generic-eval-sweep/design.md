## Context

`evals.ab` already executes and compares arbitrary current-tree legs; only argument construction is axis-specific.

## Goals / Non-Goals

**Goals:** One configuration sweep path for scalar and boolean `evals.run` options.

**Non-Goals:** Change scoring or add a benchmark gate.

## Decisions

Parse `--sweep NAME=VALUE[,VALUE...]`, validate the name, and translate boolean values to argparse's `--name`/`--no-name` spelling. The first value is the baseline. Keep single `--preset` as a ref-comparison passthrough.

## Risks / Trade-offs

- The specialized RLM spread/token report is removed → the generic harness keeps the core recall/precision/anchoring comparison and can be repeated externally when noise measurement is needed.
