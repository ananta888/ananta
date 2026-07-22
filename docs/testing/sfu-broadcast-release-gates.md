# SFU broadcast staged release gates

The central manifest separates five stages:

- pr: todo/schema consistency, focused Python contracts, Python and Angular
  lint, Angular unit tests and build, and migration-head coverage;
- container_browser: real pinned supply-chain and accessibility/browser runs;
- nightly: deterministic fuzz, real media-plane fuzz, scale, a minimum
  two-hour soak and chaos;
- game_day: an explicitly operator-approved atomic rollback exercise;
- release: source-bound aggregation of all required artifacts.

The matrix runner uses argument arrays rather than a shell, bounded CPU, memory
and wall-clock limits, deterministic source/config plans, per-gate artifacts
and an owned compose-project token. Cleanup executes from a finally block for
success, failure, timeout and cancellation. A missing command, backend,
artifact, schema match, passed status or cleanup result fails the stage.

Scale, soak and chaos commands validate externally produced real-runtime
evidence. They do not turn a packet capture, mock, shortened run or prewritten
status into a pass. The scheduled workflow therefore remains blocked until the
pinned environment has produced those raw results.
