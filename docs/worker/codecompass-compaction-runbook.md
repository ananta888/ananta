# Compaction runbook

1. `POST /api/codecompass/layers/compact` with `dry_run: true`.
2. Confirm the plan lists more than one layer.
3. Repeat with `dry_run: false` while no other publish is in flight.
4. Effective view before/after must match. A generation conflict means
   rebase and retry; the old head stays readable.
