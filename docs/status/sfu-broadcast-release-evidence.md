# SFU broadcast release evidence

The final release runner is an aggregator, not an evidence generator. It
requires exact cross-track dependency equality, a monotone evidence-manifest
version, fresh schema-valid artifacts with matching content and source/config/
lockfile/image/infrastructure digests, and verified signatures only when an
attestation profile is configured.

A release decision also requires:

- parent decision go at an active rollout stage;
- every required regression, fuzz, chaos, soak, capacity, operations,
  accessibility, privacy, security and supply-chain artifact passed;
- zero open critical/high security, privacy and child supply-chain findings;
- a derived receiver cap with SLO and resource budgets and exact browser,
  SFU and TURN versions;
- operator-approved, real game-day evidence showing atomic rollback,
  kill-switch success, all advanced flags disabled and complete cleanup.

Missing, partial, stale, expired, conflicting or foreign-digest evidence
produces no_go. The failed report has an empty released scope, receiver cap
zero and activation disabled. It contains only stable reason codes, counts,
versions, budgets and digests.

## Current blocker

The repository intentionally contains no fabricated release artifact. Real
container, browser/device, media-plane, supply-chain, scale, two-hour soak,
chaos and operator game-day evidence must be produced in the pinned external
environment. Parent no_go or observe_only remains an unconditional activation
block.
