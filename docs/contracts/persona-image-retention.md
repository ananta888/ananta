# Explicit automatic retention of retired persona images

The Hub can schedule deletion of **one exact, already retired image bundle**.
There is no age-based scan of arbitrary artifacts, no implicit default expiry,
and no deletion of active images. The existing revocation API must retire an
image first. Preview, publication, recording and model-training permissions do
not grant retention administration.

## Headless API and activation

Under `/api/persona-media/v1/projects/{project}/images/{image}/retention`:

- `PUT`: `{ "asset_revision": 3, "expected_revision": 0,
  "delete_after_seconds": 86400 }` installs a new durable grant. Values are
  strict integers; the delay is 60 seconds through 365 days. Asset revision
  comes from the existing revocation/purge status, not an assumed constant.
  `expected_revision=0` requires no existing retention record; replacement
  consumes the exact existing retention revision.
- `GET`: returns only retention revision, pinned asset revision, server-derived
  due time, state and attempt count. No file paths, credentials or image bytes.
- `DELETE`: `{ "expected_revision": 1 }` cancels a scheduled/running/blocked
  grant with compare-and-swap. Cancellation consumes its revision.

All three endpoints require authenticated user project `MANAGE`, use the
existing bounded/no-store HTTP boundary and work without a UI or human gate.
Workers and service credentials cannot create grants. Scheduling additionally
requires actual project membership without the original user's admin bypass.
Future attempts reconstruct only that scoped, non-admin subject and recheck
current project authority at every erasure checkpoint. The grant preserves no
Bearer token, password, historic admin role or persistent publication right.

The existing `ANANTA_PERSONA_IMAGES_ENABLED=1` composition installs the API and
SQL repositories. Automatic execution additionally requires the explicit Hub
flag `ANANTA_PERSONA_RETENTION_ENABLED=1`. This is off by default. Enabling the
flag alone grants no deletion of unscheduled assets. Disabling it stops future
automatic ticks on the next service restart but is **not** grant cancellation;
cancel individual grants before re-enabling if they should not resume.
No live project's retention grants or flags were enabled during implementation.

## Queue, fencing and recovery

The lifecycle-owned Hub tick checks at most five due grants every 60 seconds;
the reusable runner accepts 1–10 and stops starting more items after a
five-second batch admission budget. This is not a five-second hard deadline
for all filesystem/database operations. Deployments still need bounded DB
pool/lock and local storage I/O behavior. A running attempt has a 60-second
lease checked before deletion. Shutdown signals stop new work and invalidate
checkpoints; no test needs a human to unblock it.

Every attempt creates a normal `persona_image_retention` Hub task, with a
closed content-free context and an exact SQL claim. This is private Hub-store
maintenance: the Hub executes the storage operation because worker containers
must not receive the Hub artifact volume or an independent deletion authority.
It does not create a worker-owned scheduler or bypass the Hub task queue.
Task cancellation, expired leases, policy revision changes and concurrent Hub
claims cannot authorize another attempt's operations or terminal receipt.

SQL compare-and-swap allows only one claim of a grant revision. A crashed
attempt is reclaimed after lease expiry, its old task is failed, and a new
task/lease is used. The immutable asset digest and exact scoped tombstone
remain bound. Already deleted files are tolerated only through the existing
descriptor-confined eraser; an interrupted directory `fsync` is retried even
when the file is now absent. Transient classified storage failures use bounded
backoff; five attempts, including crashes, exhaust the grant. Policy denials,
changed file content, symlinks, hardlinks and integrity mismatches block it.
Renewal after a blocked failure requires a new explicit revision, not a silent
security override or a wait for human input.

Cancellation prevents further authorized operations; it cannot restore a file
whose unlink already completed. Per-file checks and the existing cross-Hub
catalog storage guard remain in force. A cancelled retention record can thus
coexist with an already partially/fully purged asset; query the separate purge
status for physical cleanup state. Audit/tombstone metadata is deliberately
retained. No secure-device erasure, backup deletion, global account erasure or
production release evidence is claimed.

## Structure and tests

Administration, claim execution, Hub task adaptation, SQL persistence and the
background tick are separate responsibilities (SRP). Narrow authority, catalog,
grant, claim and erasure ports permit isolated tests (ISP/DIP). Retention HTTP
handlers use a child blueprint with inherited authentication/privacy behavior;
common input helpers were extracted from the older mixed persona route module.
The old module still groups image/profile routes; this preserved SRP debt does
not grow with the new retention feature.

Tests use temporary files and real SQLite transactions, deterministic clocks,
synthetic source policies, actual project membership and normal Hub tasks.
They cover concurrency, stale claims, membership revocation, cancellation,
crash recovery, finite retries, transient fsync failure, closed API bodies and
automatic lifecycle shutdown. They are technical tests, not production gates.
