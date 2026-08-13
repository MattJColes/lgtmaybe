## REMOVED Requirements

### Requirement: Replying in a finding thread is GraphQL
**Reason**: Human replies no longer invoke lgtmaybe; only verified resolve-on-fix needs the GraphQL reply primitive.

**Migration**: Push a fix commit for incremental verification or use `/ask` for a deliberate question.

## ADDED Requirements

### Requirement: Resolved findings receive a GraphQL reply
<!-- anchor: github.reply-in-thread -->
`reply_in_thread` SHALL post a reply on a known review thread via the GraphQL
`addPullRequestReviewThreadReply` mutation. It SHALL be used only after
resolve-on-fix has verified that a finding disappeared and resolved the thread;
human comments SHALL NOT invoke it.

#### Scenario: a verified fix resolves a finding
- **WHEN** resolve-on-fix resolves a thread whose finding disappeared
- **THEN** `reply_in_thread` posts `✅ Looks resolved.` to that thread via GraphQL
