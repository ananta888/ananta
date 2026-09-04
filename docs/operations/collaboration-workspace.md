# Collaboration workspace operations

The native core is off by default. Enable it only on the Hub:

```text
ANANTA_COLLABORATION_WORKSPACE_ENABLED=true
ANANTA_COLLABORATION_WORKSPACE_STATE=data/collaboration-workspace.sqlite3
```

The additive API is `/api/collaboration/workspaces`. Disabling the flag removes the service while leaving
stored data and all legacy ShareSession paths untouched. Buzz is not required and its default adapter reports
`disabled`; it never changes native-core availability.

Run `python scripts/check_collaboration_workspace_boundaries.py` and
`pytest -q tests/collaboration_workspace` after changes. The suite is deterministic and requires no browser,
network, external relay, prompt, checkbox or person. The Angular route is `/collaboration` for authenticated
Hub users.

The native browser gate is also fully automatic and starts an isolated real
Hub plus Angular application. Use a free port and temporary result/state paths:

```text
cd frontend-angular
E2E_PORT=4309 E2E_RESULTS_DIR=/tmp/ananta-collaboration-e2e-results \
E2E_PID_FILE=/tmp/ananta-collaboration-e2e-pids.json \
E2E_DATA_ROOT=/tmp/ananta-collaboration-e2e-data \
npx playwright test tests/collaboration-workspace-native.spec.ts --project=chromium --workers=1 --retries=0
```

The E2E composition explicitly enables the otherwise default-off native core
and binds its SQLite state below the isolated test data directory. It never
waits for approval or other human input. The test covers Workspace and Room
creation, durable message and reply, read cursor, archive/reopen, reconnect and
an automated WCAG scan. This remains local technical evidence until a Hub run
was reserved before execution and completed in the Evidence Registry.

## Profiles and boundaries

- `local`: SQLite + Hub relay, no SFU, TURN, Buzz or external secret required.
- `single_hub`: the same durable boundary with operator-managed storage and backup.
- `multi_hub`: `unverified`; requires shared CAS, outbox, presence/cache and tested split-brain fencing.

The PostgreSQL shared-event and coordination repositories are tested with
`COLLABORATION_POSTGRES_TEST_URL`. The gate upgrades to the current Alembic
head, exercises downgrade/upgrade, uses two repository instances for concurrent
append/checkpoint/control/cache CAS, and verifies tenant, workspace and room
isolation. Concurrent Alembic invocations are serialized. Absence of this
explicit test URL skips only that database-specific gate; it never converts a
SQLite observation into Multi-Hub evidence. A shared-adapter failure propagates
as a bounded request failure and never switches writes to process-local state.

The fixed `collaboration-local` Hub-evidence profile binds the collaboration
sources, repository revision, execution environment and PostgreSQL test URL
before pytest starts. Its latest committed report is
`artifacts/domain/collaboration-local-hub-evidence.json`. This report has
`local` scope only; it cannot satisfy a production canary, target-hardware,
real-device Live/SFU or pinned Buzz runtime gate.

Do not enable SFU from an `observe_only` or `no_go` state. Do not present a
successful local test as Live/Buzz production evidence. The three release lanes
are independent.

## Read-only diagnosis

1. Run `python scripts/check_collaboration_workspace_boundaries.py`.
2. Inspect the capability endpoint and stable `reason_code`; never log payloads,
   prompts, keys, nonces or message text.
3. Inspect SQLite with `PRAGMA integrity_check`, outbox status counts, projection
   checkpoints and search drift. Do not modify rows as a repair.
4. Compare queue depth, replay count, projection lag, revocation latency and
   reconnect/loop-rejection counters against the configured profile.

The authenticated `GET /api/collaboration/workspaces/<workspace_id>/operations`
endpoint returns the content-free aggregate snapshot and threshold alerts. It
does not expose actor, room, event, payload, prompt, secret, key, nonce or
message-text dimensions.

Content-safe metrics use only deployment profile, adapter kind and bounded
reason code as dimensions. Actor, room and event IDs are deliberately excluded
to cap cardinality.

## Backup, restore and disaster recovery

Use `CollaborationRecoveryService.backup` to create a transactionally consistent
SQLite copy and record its SHA-256 digest and event sequence. Restore requires
that exact digest, passes SQLite integrity/schema checks, atomically moves the
current database plus WAL/SHM files to `.rollback*`, and then rebuilds timeline,
thread, search and other projections from canonical history. A failed digest or
integrity check leaves the live database untouched.

The deterministic DR test proves RPO at the recorded backup sequence and
standalone rebuild without Buzz. Environment-specific RTO must be measured and
recorded for the deployed storage/hardware profile; it must not be inferred from
unit-test timing.

## Safe rollback order

1. Disable the optional Buzz bridge; native admission continues.
2. Disable optional SFU selection and retain the explicit Hub-relay fallback.
3. Disable indexed projections/UI surfaces if drifted; canonical events remain.
4. Disable `ANANTA_COLLABORATION_WORKSPACE_ENABLED` only if the native API must
   be withdrawn; do not delete the database or legacy ShareSessions.
5. Restore a verified backup only after read-only integrity and digest checks.

No standard repair uses row deletion, forced gate success, invented evidence
IDs, `--no-verify`, or a human waiting step.

## SLO signals

Alert on bounded percentiles/counts for admission latency/errors, outbox queue
and terminal failures, projection/search lag or drift, replay conflicts,
revocation latency, reconnect storms and bridge loop rejection. Enabled live or
bridge adapters add their component health; disabled adapters are not failures.
