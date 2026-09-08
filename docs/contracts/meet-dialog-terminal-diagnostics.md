# Content-free terminal dialog diagnostics (MAP-12/32)

Source audit: Ananta `5807546ba`. Source controls and task termination already
use Hub-owned revisions/CAS; phase observation is separate from authorization.
However the standalone dialog child discards stdout/stderr and reports only
`completed`/`failed` through the existing finish callback. The useful closed
failure/timing observations currently exist only in private test adapters.
Do not add arbitrary exception text, browser console dumps or media to logs.

Implement an additive, execution-only terminal observation path:

1. A small shared closed contract accepts a fixed stop-reason enum and bounded
   integer process-family measurements (elapsed time, CPU and separately named
   self/terminated-child maximum RSS). It must distinguish those measurements
   from aggregate concurrent memory, GPU use, delivery and release evidence.
   Unknown fields, booleans-as-integers, nonfinite/out-of-range values, arbitrary
   messages and evidence/authority claims fail validation.
2. A Worker adapter optionally measures one already assigned child run and
   sends at most one small signed report after source cleanup and the existing
   finish callback. Reuse the fixed operator-configured Hub origin, with a
   separate versioned endpoint/signature domain and a one-second deadline.
   No polling thread, retry, model/GPU probe, altered grant or fresh assignment.
   `MEET_DIALOG_DIAGNOSTICS_ENABLED=1` explicitly enables this optional path;
   old Hub/Worker exchanges retain their exact v1 shapes. Reporting failure
   cannot hide or change the original runtime outcome or delay media cleanup.
3. The Hub accepts only the exact task/dispatch lease/runtime binding on an
   already terminal ordinary dialog Task, within a short fixed cleanup grace.
   A narrow repository adapter stores one bounded observation alongside the
   locked terminal Task. Identical reports are idempotent; conflicting reports cannot
   replace it. Persisted data is explicitly an unverified Worker observation,
   never a task/status/policy/lease transition, SFrame proof or SRC/RUN evidence.
   Hub-derived status/control revision remain separately labeled facts.
4. A separate owner/project-scoped read endpoint returns only that closed
   projection or explicit absence. Do not add fields to the strict existing
   phase, assignment, finish or control schemas. Configure the auxiliary service
   beside the existing Hub dialog bootstrap; do not add another responsibility
   to `MeetDialogService` or let a Worker select/authorize recipients.
5. Test schema mutation/redaction and measurement semantics with deterministic
   clocks/ports; test real signed HTTP, exact SQL snapshot/replay/tenant/terminal
   fencing and expiry. Exercise the actual child completion path without
   human input. Missing observations must stay visibly missing, never become
   a successful media or production claim. Then add UI/operational presentation
   using existing source/status components as a separate verified increment.

SRP/ISP/DIP: measurement, authenticated transport, Hub admission, persistence and
read access are distinct small adapters. The broad existing dialog runtime and
bootstrap composition remain preserved debt, not homes for parsing, SQL or
telemetry policy. No schema change to Meet and no production flag activation.
MAP-12/32 stay open until their remaining UI, stop, resource and rollout criteria
are verified; this slice alone does not establish GPU metrics or a stop SLA.

Persistence refinement before implementation: `meet_task_write_validation.py`
already makes the complete terminal `worker_execution_context` immutable. Do
not weaken that fence to append diagnostics. Use a separate append-only Hub SQL
ledger, one row per original Task, and revalidate/lock the exact terminal Task
snapshot in the same transaction as observation insertion. Identical immutable
records remain idempotent; no Task status/context/event mutation is required.
Read access rechecks current Task ownership/scope and the stored binding digest.
Terminal identity immutability tests must remain unchanged and pass.

Transport-budget refinement: an urllib socket timeout alone does not bound DNS
or a response that dribbles data. Install a one-shot POSIX real-time deadline
only in the existing standalone dialog child's main thread, after source and
browser cleanup. Cap it at one second and the original assignment cleanup
deadline, restore the prior signal handler, and skip optional reporting if a
timer is already owned or the environment cannot provide this guard. Never
replace another timer, create an unbounded reporting thread or extend the
executor's existing watchdog. Exercise blocking DNS/body stand-ins in owned
real child processes, not by changing the test runner's signal state.

Implemented admission lifetime: requests must be at most ten seconds old (two
seconds clock skew forward permitted), and insertion must precede the original
assignment deadline plus thirty seconds. Early termination does not start a new
thirty-second timer: the original assignment bound remains authoritative. The
Worker itself reports only immediately after cleanup, once, within its original
five-second executor cleanup allowance; receipt grants no execution rights.

Verification before packaged integration: 227 backend checks passed in 153.52s,
including real Worker HTTP to Flask/SQL, separate request/response HMAC domains,
current owner/project checks, immutable terminal Task/history, exact duplicate
handling, conflicting/stale reports and the unchanged 44 terminal-reopen tests.
Real owned children verify DNS/body stalls and redirect denial; the one-second
guard restores signal ownership and skips existing timers/non-main threads.
16 existing bootstrap/budget checks passed in 16.81s. The first SQL pass found
four failures caused by catching domain MeetError (a ValueError subtype) as a
generic bad record; domain error codes/statuses are now preserved and tested.

The separate Angular display makes no request on load, performs only one bounded
owner read per action, and discards pending/previous data on identity/task/scope
changes. Missing measurements remain absent, not zero; CPU/RSS labels explicitly
exclude aggregate concurrent memory, GPU and media-delivery/release evidence.
111 focused diagnostics/phase/dialog UI tests passed in 3.51s. Angular template
type checking passed with one pre-existing unrelated RouterLink warning.
These are local technical checks, not Hub-issued production release evidence.
