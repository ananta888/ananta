# Persistent dialog dispatch replay boundary

## Source check and next verification slice (MAP-11/29)

`DialogExecutor.start` validates a closed assignment, reserves its dispatch
lease in `/state/dialog-leases.sqlite`, then launches the one delegated runtime.
The Compose worker has a private persistent `/state` mount. Reservation survives
normal process completion and uncertain launch; cleanup removes only rows older
than their original deadline plus the replay margin. The existing focused test
covers a failed watchdog start and a retry on the same executor object, but not
an independently restarted process or simultaneous executors.

Add bounded, fully headless process-level verification against a real temporary
SQLite file: two independent executor processes race for one assignment; a
process exits abruptly after durable reservation but before launch; a fresh
process then retries that exact assignment. Expect only one accepted launch
boundary, or none after the uncertain crash, and a fixed replay rejection on
subsequent starts. An independent lease remains admissible. No retries, release
of uncertain reservations or production state migration are introduced.

Test-owned process pipes/events and short joins bound every observation and
cleanup. The launch/watchdog boundary is a deterministic double: these checks
verify durable local dispatch admission, not real browser receipt, container
death, cross-worker-volume replay protection or public production recovery.
Existing real browser watchdog and three-renewal gates remain separate. Keep
MAP-11 open until its broader acceptance criteria are actually met.

Do not refactor production merely to make the new test pass. If a real defect
is exposed, document its cause and make a narrow contract-preserving correction.
The existing executor still combines replay storage, process launching and slot
bookkeeping (SRP/DIP debt); tests must isolate those effects explicitly, without
adding worker policy or an orchestration loop.
