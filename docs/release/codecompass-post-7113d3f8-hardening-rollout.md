# CodeCompass hardening rollout after `7113d3f8`

## Architectural boundary

The hub owns authorization, scope, task creation, dispatch bindings, result
admission, CAS publication, migration journals and rollback decisions. Workers
only execute a bound layer or retrieval job and return immutable artifacts.
Workers never write a hub queue or layer head and never dispatch another
worker.

`CodeCompassLayerDispatchBackend` is the hub adapter. It records a deterministic
intent before queue dispatch and admits a result only when task, assignment,
lease, intent digest, input revision, profile digest and the complete artifact
set match. The publisher remains a separate CAS port (SRP and DIP).

## Compatibility migration

Run `CodeCompassHardeningMigrationService.run(dry_run=True)` first. The report
contains only stable identifiers, scope fields and digests. It excludes
credentials, repository content and environment paths. Inventory adapters cover:

- legacy v1 single-layer heads
- the legacy global DuckDB active pointer
- installation-wide GitHub credential references

An apply run requires the explicit migration write switch. Its deterministic
migration ID and per-operation journal make retries idempotent and resume after
the last completed operation. Keep old resources read-only until the complete
gate passes. `rollback(migration_id)` invokes each adapter in reverse operation
order and restores the last consistent read-only state.

## Staged rollout

1. Shadow/read-only: use scoped reads, build migration inventory and compare
   effective-view digests. Layer and migration writes remain disabled.
2. Opt-in writes: enable layer dispatch for selected tenant/repository scopes.
   DuckDB remains opt-in through provider selection. Keep RLM default-off.
3. Limited production: admit worker results and scoped DuckDB pointers only for
   canary scopes; monitor denials, CAS conflicts, stale revisions, incomplete
   artifact sets, pointer mismatches and fallbacks.
4. Default eligibility: only after all P0/P1 gates are green and rollback has
   been exercised. Default eligibility does not remove per-feature kill switches.

## Kill switches and recovery

- Layer writes: construct the dispatch backend with `writes_enabled=False`.
  Existing heads remain readable.
- Migration writes: construct the migration service with
  `writes_enabled=False`; dry-run remains available.
- DuckDB: deselect the `duckdb` vector provider. Existing scoped snapshots are
  left untouched.
- Recursive RLM: keep `codecompass_rlm_enabled` false. The bounded retrieval
  fallback performs one capability-bound request.
- Claim extraction: remove the optional LangExtract adapter. Admission remains
  available for deterministic adapters and unknown `SRC_*`/`RUN_*` IDs remain
  rejected.
- GitHub: disable the GitHub authorization provider composition. Stored refs do
  not expose tokens; repository-scoped minting can be re-enabled later.

Never repair a head or pointer in place. Stop new writes, retain consistent
reads, inventory state, resume or roll back the journaled migration, then rerun
the deterministic gate.

## Known limits

The migration core intentionally depends on inventory, journal and writer ports;
deployment-specific filesystem and secret-store adapters must be composed by the
hub. No local in-process worker shortcut is provided.
