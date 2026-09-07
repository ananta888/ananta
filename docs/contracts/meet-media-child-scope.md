# Audio/media child scope (MAP-09/20)

Pre-code audit after `9adeedffd`: dialog sessions now retain their authoritative
parent organization and recheck its lifecycle. Their ASR children and generated
chat/media reply tasks still explicitly set tenant/project without copying the
organization group. `MeetDialogAudio.current` checks dispatch context but not
the child's parent column/organization tuple. One-shot publication lease checks
and media completion also lack this explicit parent lifecycle fence.

Carry the full already-authorized parent tuple through the ordinary Hub queue
for audio and media child tasks, including the separate team argument. Reuse
the existing narrow Hub lifecycle port, not generic ingestion fallback that may
drop scope on lookup failure. Bind audio reservation CAS to the snapshot's
parent/organization fields; reject missing/stale parent before reservation.
Recheck exact audio-child linkage and scope before accepting samples/results.

For generated media, validate the parent before ingestion, during existing
capacity/current checks, at successful completion and on publication lease
reads. Inactive/missing/mismatched parents must deny dispatch/result/lease, while
failure/cancel cleanup still works. Standalone project previews and existing
worker contracts remain compatible; no new capability, source or identity.
Do not claim that these checks instantly cancel arbitrary in-flight native
model execution; stale output must not be published or returned as success.

Reproduce absent child inheritance first using the real SQL organization graph.
Verify all four inherited fields, cross-scope/parent tampering, pre-reservation
denial, exact CAS under scope change, revoked media completion/lease and cleanup;
regress ordinary audio, turn, capacity, persona and chat paths. Repeat the private
parent/organization browser composition with actual generated child rows checked.
Policy/models remain synthetic. Role-assignment eligibility, distinct Meet
agent principals, general browser privacy and production gates remain open.

SRP/DIP: reuse the small lifecycle port for parent policy and leave queue/CAS in
the persistence adapters. The existing mixed `meet_turn_service` module remains
documented SRP debt; this slice does not introduce another scheduler or move Hub
policy into the Worker.

## Implemented and verified (2026-09-08)

Both original SQL inheritance assertions failed before correction (two failures,
11.62 s): audio and generated media children had an empty organization tuple.
Both now carry organization/unit/team/role from the authoritative dialog/parent.
Audio reservation checks its live parent first and fences parent/scope in CAS;
audio result admission rechecks the exact child parent and scope. Media capacity
authority and publication leases recheck parent lifecycle. Successful media
completion also fences the observed child scope in CAS, and uncertain policy/
lookup rejects success without exposing provider details. Failed/cancelled
cleanup remains possible after revocation.

The first combined run found one older chat test with a synthetic parent string
but no actual task row (90 passed, one failed). Its fixture now creates the real
minimal parent/project; the production check was not weakened. The existing
synthetic audio fixture likewise explicitly carries its real contract's parent
column. Final targeted regression: 222 passed in 86.81 s, including audio and
media child SQL/CAS races, removed scope, revoked parent/organization, no
pre-reservation ingestion, stale completion/lease, provider failure, task
cleanup, chat, capacity, image/video/persona profiles, dialog lifecycle and
avatar/voice negotiation. Ruff and the 77-file Worker boundary gate passed.

The current private Hub/Worker/Meet lifecycle matrix passed both cases in
55.13 s. Each now asserts that both generated chat replies are completed real
media child tasks with the entire organization tuple. Moving remote screen and
two correlated chat replies precede parent cancellation or organization pause;
Worker stopped in 1038.16/167.92 ms and the remote participant disappeared,
without a dialog-stop command initiating teardown. This verifies real transport
and persistence with synthetic organization/model/policy, not GPU, public TURN
or production release evidence. It does not establish instantaneous cancellation
of native inference already in flight.

Role-assignment eligibility/revision and distinct Meet agent identity remain
open; neither MAP-09 nor MAP-20 nor the whole TODO is complete. The legacy
`MeetTurnService.lease_allowed` still uses the repository service locator (DIP
debt); parent policy itself lives in the reusable narrow lifecycle port, while
queue and CAS remain Hub persistence responsibilities.

Follow-up fixture audit: the opt-in legacy GPU chat test used the same missing
parent assumption as the corrected CPU chat test. Its isolated preparation now
creates the exact project and active Hub parent before requesting GPU work.
`test_meet_gpu_chat_fixture.py` exercises that preparation and the actual media
task start/current/completion entirely on CPU: one passed in 7.78 s. The two
actual hardware tests were explicitly skipped in this fixture-only run; no GPU
success is claimed. The selected-voice combined GPU gate still requires 4096 MiB
free; the last read-only observation was 1464 MiB, with other GPU work untouched.
