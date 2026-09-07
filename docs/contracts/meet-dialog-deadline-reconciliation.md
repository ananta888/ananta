# Hub-owned settlement of expired dialog tasks

## Source check (MAP-09/11/29)

`MeetDialogAuthority.current` rejects an expired parent and the Worker/browser
watchdogs stop media. Normal runtime teardown calls `finish`; however a hard
Worker crash or unavailable callback can omit that call. `MeetDialogService`
inspection currently reads task status without settling expired tasks, and no
existing Meet Hub lifecycle service reconciles these task kinds. Therefore a
deadline-expired parent can remain `in_progress` indefinitely. Audio children
have independent shorter deadlines but similarly depend on callback cleanup.

Add cleanup only, owned by the already enabled Hub dialog subsystem:

- Scan only active `meet_dialog_session` and `meet_audio_receive` tasks, through
  a bounded keyset page. A per-run cursor is a scanning optimization, never an
  ownership or authorization record. Restart can safely begin the scan again.
  Do not use offset pagination over rows being removed from the active set.
- Validate the captured tenant/project/task-kind, dispatch/runtime and exact
  context. Missing/malformed deadlines or bindings are diagnostic failures,
  never guessed values or authority to mutate an unrelated task.
- At or after the original absolute deadline, use the existing Hub task CAS
  and audit path to mark only the unchanged active task failed. A later callback,
  concurrent cleanup or changed assignment cannot override a terminal result
  or settle a replacement. No new dispatch, source, grant or renewal occurs.
- Scan children independently, so a crash between parent settlement and child
  cleanup cannot leave an expired child stranded. An old parent audio pointer
  grants no authority over a different child's lease/runtime. Ordinary early
  cancellation keeps its existing direct cleanup path.
- Compose a narrow repository/read-CAS port, deterministic reconciler and Hub
  lifecycle tick separately (SRP/ISP/DIP). The manager starts it only for enabled
  Hub dialog composition, never on a Worker or implicitly in a test. Bound the
  page and stop checks, redact errors and allow clean shutdown.

This is eventual task bookkeeping recovery, not a replacement for the browser's
2.5-second security watchdog or proof of immediate process death. Scan latency
depends on the bounded page size and number of active sessions; do not claim a
fixed global settlement deadline for unbounded task counts. It does not solve
all reconnect, dispatch idempotency, multi-agent or production-soak criteria.

Verify real isolated TaskQueue/SQL CAS, independently expired audio children,
unchanged live/foreign/malformed tasks, two reconcilers, stale snapshots,
terminal callback races, cursor progress/reset, restart and lifecycle role/
shutdown behavior. No production database cleanup or service restart is part
of implementing or testing this slice.

## Implementation boundary

`MeetDialogDeadlines` owns deadline validation, a maximum 100-row call budget
(25 in the lifecycle tick), cursor advancement and an actual-clock/shutdown
recheck inside settlement. It depends only on the two-method
`DialogDeadlineStore` port. `SqlDialogDeadlines` owns keyset reads and exact
snapshot comparison; the bootstrap explicitly injects the existing task-status
CAS, including its normal audit and post-commit behavior. Its read engine and
that CAS are composed against the same Hub database, not separate stores.

The optional Hub lifecycle service ticks every five seconds, contains failures
using only exception class names, reports malformed-row counts without task
identifiers or context, and registers its stop event/thread with the existing
shutdown manager. Tests and Workers never start it implicitly. It neither
loads media nor contacts a browser, provider or Worker.

The new separation protects SRP, ISP and DIP. Existing task-runtime global
repository composition is preserved behind an explicitly injected CAS port;
this slice does not refactor that cross-project infrastructure. PostgreSQL
uses the existing row lock; single-process SQLite tests use the existing local
mutation lock. No multi-Hub SQLite serialization guarantee is added.

## Verification, 2026-09-07

The first focused run had 59 passes and one fixture failure in 32.24 seconds:
the new audio-child ingestion lacked the synthetic owning project FK row. The
fixture now provisions it explicitly; no database constraint was disabled.
The expanded run passed 74 tests in 36.37 seconds. Final cursor hardening also
rejects an invalid persisted identifier without starving later valid rows;
the final nine-file regression passed **76 tests in 37.20 seconds**.

Coverage includes actual isolated TaskQueue ingestion, exact SQL/CAS snapshots,
independent child settlement after parent cleanup and a fresh reconciler,
single audit events despite replay, unchanged live/unrelated/malformed tasks,
tenant/project/dispatch/runtime/deadline/terminal races, original-clock and
shutdown rechecks, keyset deletion/reset, malformed-ID progress, role/opt-in
composition, bounded thread shutdown and redacted failure logging. These are
technical synthetic tests, not production release evidence or a live outage.
