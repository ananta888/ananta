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
