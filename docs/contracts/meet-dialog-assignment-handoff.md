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
