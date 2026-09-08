# Bounded dialog assignment handoff (MAP-10/11)

## Source audit and next implementation

At `e01d0a05c`, `DialogExecutor.start` durably reserves the dispatch lease,
starts a child with a pipe, writes the complete assignment synchronously,
closes the pipe, and only then starts its deadline watchdog. A child that
does not read stdin can therefore prevent watchdog installation when the
assignment exceeds the available pipe capacity. A bounded HTTP connection
does not bound this separate server thread. Do not interpret an HTTP timeout
as authority to replay the reserved assignment.

First exercise the real executor against a test-owned child that deliberately
does not read, with a legal small Linux pipe and a valid maximum-length grant.
Use an independent parent deadline and clean up only owned child processes.
This is a synthetic startup test, not real browser/media crash evidence.

If reproduced, replace only the startup input handoff with a narrow launcher:
serialize and bound the closed assignment, prepare an anonymous temporary
input file, rewind it, and give the child that descriptor as stdin. Close the
parent descriptor on success and failure. No grant in argv, environment,
named artifacts, logs or a shared runtime directory. No waiting for a child
read or readiness ACK, no additional thread, retries or Worker authority.

Keep original command, process-group isolation, output redaction, durable
reservation, current slot bounds and original-deadline watchdog. Uncertain
launch still consumes the lease; watchdog-start failure still kills the owned
process group and releases only the local slot. Verify actual child receipt,
non-reading startup, size/IO/launch failures, descriptor cleanup, replay after
failure and existing process-level admission races.

Separating input/launch IO from replay/slot ownership protects SRP and DIP.
The existing executor still composes persistence and watchdog bookkeeping;
do not expand that refactor or claim general high-availability recovery.
Storage/kernel stalls remain outside application-level timing guarantees.

## Reproduction and chosen structure

The real executor regression failed in 11.88 seconds overall: the valid grant
exceeded a legal 4 KiB Linux pipe, and startup blocked until the non-reading
owned child exited after three seconds. Its buffered `stdin.close()` then
raised `BrokenPipeError`; the deadline watchdog had not started.

Use a context-managed input adapter, rather than moving the entire launch
into another abstraction. The executor retains its process reference before
the input context closes, so even a close failure after successful spawn
still reaches the existing owned-process cleanup. The adapter bounds bytes,
performs one unbuffered write before launch, rejects short writes, rewinds
and closes the anonymous descriptor. This smaller seam preserves SRP and
keeps process/slot cleanup ownership explicit.

The runtime also closes its inherited input immediately after the bounded
read, before constructing a Hub client or browser/model descendants. Otherwise
the new seekable descriptor would retain the grant for the entire session.
This closure applies to malformed input as well. It is not permission to log,
persist or forward the parsed assignment beyond its existing scoped protocol.

## Verification

The initial post-fix process/replay/pump regression passed 15 tests in 16.42
seconds. The expanded Worker/handoff/health/media/session regression passed
all 200 tests in 82.97 seconds. After grouping the final assertions correctly,
all 14 input tests passed again in 15.01 seconds. Coverage includes the actual
non-reading child, exact anonymous-file bytes received by a separate process,
zero-link private mode-0600 input, parent descriptor closure, runtime closure
before client/browser creation, invalid/oversize input, short/failed writes,
seek/consumer/launch failures and cleanup after post-spawn close failure.
Existing independent-process replay/crash and watchdog-start failure gates
remain green; none grants a retry of an uncertain dispatch.

Targeted Ruff and the standalone 80-file Worker boundary check pass. Tests
use owned SQLite files, synthetic assignments and bounded local processes;
no existing Worker or database is changed. This fixes startup handoff, not
automatic reconnect, real multi-agent media crash recovery, GPU delivery or
production release evidence. The earlier health source-packaged image was
built before this change and is not claimed as its runtime acceptance image.
