# Ornith 1.5 rollout

Current decision: evaluation infrastructure is available; production rollout
is blocked. The 9B and 35B profiles are experimental and disabled. The 397B
profile is unavailable on the target host.

Promotion gates, in order, are: approved license text and provenance; immutable
artifact import; runtime image digest/SBOM/vulnerability gate; parser and
capability observations; 30-minute no-OOM/no-swap-growth hardware run; vision
and CodeCompass result where applicable; then production-scoped Hub evidence.
All gates run headlessly. A policy denial returns a bounded blocked result and
never waits for human input.

Rollback disables only the affected profile, cancels new assignments and keeps
evidence/history. Existing local profiles and their routes are not overwritten.
