# Meet reference recovery after host restart

On 2026-09-09 the host boot time changed to 20:08:10 Europe/Berlin.
The previously live executor handle was missing, no matching runner/pytest
process remained and its `/tmp` worktrees and report directory were absent.
`RUN_88068c4c434679702ac991373e66298f` was still `reserved` in the persistent
Hub TEST registry. No terminal test result survived; this is not a pass or a
new reproduction of the earlier receiver freeze.

After checking the exact recorded task, source, revision, synthetic scope and
creation before the current boot, the existing Hub registry service marked
only this reservation `cancelled`, with its original assignment/dispatch
bindings. A runtime interruption receipt retains the digest input. No direct
SQL state patch, replacement identity, fabricated worker result or promotion
was used. Completed earlier reports had been retained outside `/tmp` and
remain available. Four individually inspected, stopped browser/STUN/TLS
containers created for this run were removed without force or volume deletion;
their exact internal network was removed only after it was empty.

## Reconstructed private reference

New isolated worktrees live under a uniquely created `data/` runtime directory,
not `/tmp`. Ananta is frozen at `5a7be27f5`, Meet at `c4ef486`, browser image at
`sha256:5d4be51c5dda34636602c7a4782480d8f930a19f2d186c91e7e10b661a8f4b10`.
Native Go/Coturn packages are extracted locally, not installed as host services.
No serving build or container was replaced. The rebuilt private frontend digest
is `803a77c0f5b8076bff73c6650ffc9a62c4e48db148ec5ecca43cedb2a49b85b8`;
it is a new build observation, not assumed byte-identical to the deleted bundle.

The actual private smoke passed in 34.720 seconds, one pass, no failures/errors/
skips, with unchanged inputs: `SRC_6b5a3906023621addc28d9aa17359fe6` /
`RUN_e338ee9fdd06f2db559c56b67ce2675f`. The normal two-hour diagnostic reference
follows with a fresh reservation; historical failed/interrupted runs remain
distinct. Its logs and output directory now also survive a normal reboot.

The GPU is detected again but has two different active jobs under `moe-test`;
the previously authorized test-model unit remains inactive. Those new jobs
were not stopped or repurposed. Public readiness now observes running Meet
`5a10338` and the additive integration endpoint, with auth/SFrame required and
all implemented ports listed, but machine admission still disabled. Neither
the restart nor these deployment changes were performed by this recovery.

## Reservation receipt hardening plan (MAP-32)

Persist a closed `reservation.json` before launching the selected child:
actual Hub-issued source/run/binding references, exact source/companion and
frontend digests, fixed profile identity and explicit reserved/TEST/non-release
classification. Never write the assignment projection, dispatch credentials,
full inherited environment or secret paths. Creation is exclusive and bounded
to the newly owned output directory, with a restrictive file mode and flush.
A receipt-write failure must prevent execution and follow the existing failed
result path. The receipt does not prove a live process, successful test or
authority to resume/cancel; recovery must still inspect authoritative state.

Keep serialization/persistence in a small artifact adapter (SRP), independent
of Hub identity issuance and worker execution (DIP). Test ordering, exact
fields/redaction, existing-file protection, write failure and all closed runner
profiles. Do not alter the currently running frozen diagnostic reference.
