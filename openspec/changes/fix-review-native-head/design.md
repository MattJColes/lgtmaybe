## Context
The adapter updates one summary while GitHub retains its original immutable commit_id.
## Decisions
Retain commit_id in the existing review lookup, select the latest matching review across all pages, and create a current-commit summary after required inline posting succeeds. Same-head updates and incomplete results preserve existing behavior. Explicitly bind initial completed summaries to the reviewed commit to withstand concurrent PR pushes.
## Risks
Fresh summary creation must not repost existing inline findings or omit rejected comments. Tests cover one-for-one dedupe, failures, pagination and exact commit binding.

## Authenticated attribution
Resolve the posting actor from GitHub GraphQL viewer using the actual token; reuse only its summaries and diagram completion markers. Unknown/error identity fails closed. Public tests cover Actions, PAT, self-managed and branded App actors, copied markers, real review execution and trusted cache reuse.
