# Test startup and cleanup isolation

## Incident, 2026-09-08

A diagnostic invocation imported `agent.repositories.meet_dialog_phases`
before invoking `pytest.main`. The repository package imports the database
engine and cached settings. At that point test environment setup had not
run, so the engine used the local `data/ananta.db`. Later assignments of
`DATABASE_URL` and `DATA_DIR` in conftest did not retarget those objects.
The destructive test cleanup consequently cleared the local database's
application tables. This was an agent execution error, not an authorized
reset. The diagnostic's two passing browser results are invalid evidence.

Subsequent read-only checks found the inspected Task, archive, user, project,
goal, agent, artifact and Organization tables empty. Their contents before
the incident are unknown. A post-incident copy preserves only the remaining
state; it is not a pre-incident backup and cannot establish restoration.
No pre-incident backup was found in the checked repository paths.
The inspected running Hub containers use a separate Docker data volume or
`data/hub` bind mount; neither mounts this local database as `/app/data`.
That mount check alone is not a full audit of every running service.

No database replacement or speculative restoration was performed. Recovery
requires identifying a valid pre-incident backup and its intended target;
never overwrite another runtime with the emptied database or test fixtures.

The user subsequently confirmed that this is a disposable development/test
environment, contains no production data, and requires no backup restoration.
Recovery is therefore not a blocker. The isolation guard remains mandatory;
the incorrectly isolated diagnostic is still excluded from verification.

## Preventive boundary

`tests/isolation_guard.py` checks preloaded database/settings objects before
conftest imports the application. The only accepted engine is the exact
harness-owned database: named-memory by default, or a fresh private temporary
file under explicit `ANANTA_TEST_DATABASE_MODE=wal`. Cleanup's data directory
must be the exact process-local test path. URL query ordering is normalized;
another in-memory database, a caller-selected local file, another process's
namespace or a remote database does not qualify. The guard emits a fixed error without URLs,
credentials or paths.

The engine check runs again before database initialization and before each
runtime cleanup. The directory check runs before cleanup and immediately
before filesystem artifact cleanup. Changing environment variables after an
early import is not a recovery mechanism: abort that process and start a
fresh correctly isolated test process.

The guard is a small test-infrastructure policy separate from application
repositories (SRP). Regression subprocesses exercise actual conftest startup
with an owned sentinel database, including the same early repository import,
and require the sentinel bytes to remain unchanged. No test uses the local
runtime database as a negative fixture.

Verification: 12 guard tests passed in 16.78 seconds, including the actual
early repository import in a bounded subprocess and byte-for-byte sentinel
preservation. The affected local database's modification time remained
unchanged during these correctly isolated checks. No recovery is claimed.

## Source-checked browser database follow-up

Correctly isolated browser attempts reproduced SQLite extended error 262
(`SQLITE_LOCKED_SHAREDCACHE`) on a Task control write. The named-memory
shared-cache database cannot use WAL; concurrent browser/Hub threads expose
table-lock failures that the application's file-backed SQLite WAL setup
avoids. This is not an identity-policy denial, and the failed run stays failed.

Add an explicit test-only `ANANTA_TEST_DATABASE_MODE=wal` startup option.
The harness itself must allocate a fresh private `mkdtemp` directory and
fixed filename, never accept a caller-supplied database URL/path or an
existing runtime database. Retain the exact URL/settings/directory guards.
Default unit/xdist runs remain named-memory. Test setup stays headless and
bounded; do not add business-operation retries, relax assertions or change
production transaction/policy behavior. Check real SQL initialization uses
WAL, sentinel rejection, process isolation and both legacy/new-principal
browser paths before recording this result as verified.

Both modes passed the same 21 isolation tests: named-memory in 18.85 seconds,
WAL in 129.58 seconds. The slower file mode is opt-in for concurrency gates,
not a replacement for ordinary unit-test defaults. The harness allocates its
own mode-0700 directory, caches that selection only for the current process,
and retains failed-run files for diagnosis. Separate processes get separate
directories. A real reader/writer test verifies snapshot isolation and a
successful concurrent commit using the production WAL connection setup.

The correctly isolated private browser gate then passed both legacy and
Organization-principal variants in 99.10 seconds with the original media,
chat, control, phase-persistence and stop assertions. No business retry or
production database change was introduced. This fixes the shared-cache
test-environment incompatibility; it does not establish production evidence
or fix the separate intermittent SFrame startup issue.
