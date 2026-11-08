# Release: Native-Core Gate with Buzz Independence

**Status:** Draft
**Date:** 2026-06-22
**Track:** PDCW-QA-006
**Owner:** Ananta Release

## Scope

The **Native-Core-Gate** aggregates the following release gates:

- Contracts
- Migration
- Security
- Room-/Thread-E2E
- Replay
- Search
- Accessibility
- Backup/Restore

Agent/Live and Buzz approvals remain independent additive gates. They are not merged into the Native-Core-Gate.

## Release Policy

1. **Native-Core-Gate** must pass before a native release can be published.
2. **Agent/Live Gate** is an additional, independent gate for agent/live capabilities.
3. **Buzz Gate** is an additional, independent gate for Buzz features.
4. All gates are fail-closed: any failure blocks publication.

## Canary Rollout

- Start with a small canary cohort (e.g., 1% of users).
- Monitor error rates, latency, and user feedback.
- Expand gradually if metrics remain within thresholds.
- Rollback is possible at any time via the release gate command.

## Verification

Run the following test suite before publication:

```bash
python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development
```

Exit code must be 0.
