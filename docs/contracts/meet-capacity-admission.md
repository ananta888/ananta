# Hub admission for the private media worker

When the existing media feature is enabled, Hub composition installs
`MeetCapacityAdmission` before dispatch to its configured private worker.
Task creation, ownership and terminal events remain in the existing Hub task
queue. Admission reserves capacity in the existing `worker_slot_leases` table;
it creates no worker job, background scheduler, renewal or independent task.

The operator-owned `ANANTA_MEET_MEDIA_CAPACITY_POOL` defaults to
`local-meet-media`. All Hub instances using the same private GPU resource must
share this pool and the same authoritative database. Distinct pool names are
**not** permission to oversubscribe one GPU. This mechanism does not arbitrate
unrelated Ollama, desktop, third-party GPU jobs or unintegrated workers and
does not measure free VRAM.

One active media slot per pool and at most four waiting reservations are
allowed. Database-serialized sequence order prevents newer reservations from
jumping the queue. Waits last at most ten seconds, also capped by the original
task deadline; policy and the exact current Hub task/lease are rechecked while
waiting, immediately before execution and before returning its result. The
existing domain service still performs its final result/policy/terminal CAS.
Queue-full/expired admission yields a bounded machine-readable 429; no human
approval, automatic retry or duplicate task is used.

The pool-lock table contains only the pool ID and admission counter. Each slot
contains content-free task, dispatch lease, tenant/project and original
deadline bindings. Unknown, mismatched, cancelled or already completed tasks
cannot enter the worker. A repeated reservation is rejected, including after
the original slot was released. Missing/crashed waiting callers cease to block
the head after ten seconds. An active slot expires at the worker's original
hard deadline plus five seconds cleanup grace.

After a successful HTTP return, capacity is released even if a subsequent
policy check hides the generated result. An uncertain execution exception
instead quarantines capacity through that deadline: a broken HTTP connection
does not prove that a GPU process stopped. The worker's existing single-flight
and replay guard remain independent final protections. Generation/classified
test IDs are not Registry-issued release evidence.

The SQL UPDATE lock covers reserve, promotion and release across separate Hub
connections. No network/model/policy operation runs inside that transaction.
Database connection/lock timeouts are still deployment concerns; the ten-second
wait is not a promise about a stalled database or a host with unsynchronized
wall clocks. The 32-bit queue sequence fails closed on exhaustion instead of
wrapping order. Slots are retained as runtime history, not source artifacts.

Generic worker-pool cleanup/revalidation/release now acts only on its own
`worker`/`combined` leases. It must not release a media lease using an earlier
expiry observation after that lease was promoted. Existing globally readable
worker-lease lists omit media leases so new tenant/task/dispatch bindings do
not leak through a non-project-scoped endpoint. Generic worker capacity
orchestration itself is otherwise unchanged.

The wait service depends on separate task-authority and slot ports, with
injectable clocks and wait function (ISP/DIP). SQL contention and request-side
policy waiting are separate responsibilities (SRP). Preserved debt: the old
generic scheduler still has process-local locking and direct repository/LLM
coupling; it is not reused as a cross-Hub media capacity guarantee. Direct
internal constructors retain optional capacity injection for compatibility;
the enabled Hub production composer supplies it. The proposed chat reply
service also accepts this same admission port, but its new MDS transport is
still not activated by this change.

Headless tests cover two independent SQL clients, FIFO, bounded queues/waits,
duplicate dispatch, exact scope, cancellation, restart, orphan expiry,
uncertain execution and generic scheduler ownership. An opt-in real RTX test
submits two synthetic Hub tasks concurrently and verifies nonoverlapping
actual worker calls and independent task results. This is single-host
generation admission, not a multi-participant Meet or production GPU SLA.

Reference checks: 148 media/chat/worker-pool regressions passed in 100.12
seconds. A further 58 checks including actual concurrent GPU requests and the
persona-profile turn with real HTTP Hub lease passed in 56.14 seconds. The
GPU concurrency fixture explicitly provisions its synthetic project/members
in a private file-backed Hub database, using production SQLite integrity
settings. Earlier fixture attempts exposed missing parent records and the
shared-cache in-memory database's table locking; neither failed attempt was
counted as a passed hardware gate. Private worker image remains
`sha256:b2a42d560e3a8f7f1dd72d7d7bf2ca9b53580a51906c13f91066148c59e7be53`;
no public Hub, Meet, trust or project policy was changed.

## Durable dialog publication admission

Enabled Hub dialog composition now also installs `MeetDialogCapacity` around
the exact selected publisher, including the single-publisher configuration.
The default immutable profile admits at most eight active dialog reservations,
two per publisher (matching the installed Worker's independent limit), and
384,000,000 bit/s of aggregate publication reservation. Configuration is a
strict JSON object in `ANANTA_MEET_DIALOG_CAPACITY`; callers cannot set it.
`ANANTA_MEET_DIALOG_CAPACITY_POOL` defaults to `local-meet-dialog` and must be
shared by every Hub dispatching to the same physical publishers.

Publication cost reserves every authorized output capability, even if paused:
128,000 bit/s speech, 1,200,000 bit/s avatar and 2,500,000 bit/s screen, multiplied
by the existing maximum nineteen remote room participants. Receive traffic,
protocol overhead and unrelated applications are not measured or shaped by
this reservation. Meet's separate sender-quality configuration and actual
hardware/network measurements remain necessary; unsupported sender ceilings
must not be reported as enforced bandwidth. Media/GPU generation continues to
use its separate existing single-flight FIFO.

The new SQL pool pins a digest of the operator profile. Mismatched profiles
fail at initialization and every mutation rather than permitting disagreement
between Hubs. A coordinated profile change needs drained old dialogs and a new
pool name; changing only one Hub or choosing another pool while old publishers
remain active is not a supported migration. Old profiles and lease history are
not deleted automatically.

Four FIFO waiters, a ten-second request-side deadline, exact Task/dispatch/role
rechecks after waiting and no fallback destination bound admission. An
asynchronous acceptance is not completion: its resource remains reserved.
Uncertain HTTP dispatch also retains the slot. Normal expiry is the original
Task deadline plus five seconds. On a subsequent admission, a terminal/missing
Task starts a 95-second cleanup quarantine (the existing longest pre-progress
Worker supervisor budget is ninety seconds), capped by the original expiry.
Later polls cannot refresh quarantine. This conservative policy can reject new
starts while stopped resources drain; it never requires human intervention.

Generic worker-pool mutations do not own these leases. Non-project-scoped
lease/queue/status projections omit private media and dialog reservations.
Clock agreement and bounded database operations remain deployment requirements.
Policy arithmetic, SQL serialization, bounded authority waiting and publisher
selection are separate modules/ports (SRP/ISP/DIP). Existing broad dialog and
generic scheduler composition is preserved rather than expanded into a second
scheduler. The initial 86 tests passed in 39.40 s and the expanded 143
capacity/legacy GPU/role/phase/authority checks in 59.31 s. Installed two-Worker
acceptance with this new admission is still required.

Final focused checks passed: 63 admission/router cases in 31.48 s and fourteen
generic scheduler/route ownership cases in 16.39 s. These include rejection of
an active reservation whose cost was mutated, a final authority read that
consumes the wait deadline, private status filtering and role changes during
capacity waiting. No deadlines were increased to obtain these results.

The installed two-Worker gate at Hub `2c046e039`, Worker image `bd2445226c60`
and private Meet frontend `95d0d4e` subsequently passed in 76.05 s with both
timing and the real SQL admission enabled. The report pins the exact default
policy; independent screens/personas, two actual recoveries (5,431.22 /
6,304.97 ms), interrupted synthetic speech, fresh consent, no replay and an
independent survivor were verified. Third-loss exhaustion stopped in 246.41 ms.
This is local synthetic-policy acceptance, not GPU utilization or public
network-bandwidth evidence.
