# Requirements: Payment link expiry

## Introduction

Payment links currently live forever. Finance needs them to expire so a leaked
link cannot be redeemed months later.

## Requirements

### Requirement 1: Links expire

**User story:** As a finance admin, I want payment links to expire, so that a
leaked link stops working.

#### Acceptance criteria

1. WHEN a payment link is created THEN the system SHALL set an expiry 30 days in
   the future.
2. WHEN a payment link is redeemed after its expiry THEN the system SHALL reject
   the redemption with an `ExpiredLink` error.

### Requirement 2: Expiry is auditable

**User story:** As a support agent, I want to see when a link expired, so that I
can explain a rejection to a customer.

#### Acceptance criteria

1. WHEN a redemption is rejected as expired THEN the system SHALL record an audit
   entry naming the link id and its expiry timestamp.
