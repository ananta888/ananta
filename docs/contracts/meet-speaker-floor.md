# Hub-owned room speaker floor

MAP-26 source audit, 2026-09-09: `MeetCapacityAdmission` serializes GPU
generation, not the later PCM publication. `MeetDialogSpokenReply` and
`DialogSpeechOutput` already bind current speech/chat/Meet authority and stop
on withdrawal, but separate dialog tasks can publish simultaneously. Existing
input deduplication and source controls do not constitute room turn-taking.

## Implementation sequence

1. Add a narrow durable speaker-resource repository, separate from GPU slots
   and the Hub task queue. Serialize a room with a database row lock shared
   by independent Hub processes. Bind every reservation to exact tenant,
   project, origin, room, task, dispatch, runtime, source and input generation.
   Store no chat, transcript, PCM, media key, grant or model output.
2. Admit at most one active permit, with bounded waiting, FIFO within priority
   and aging across priorities. Priority is explicit Hub policy, never a
   spoken instruction or Worker field. Expired/finished/revoked permits are
   terminal. A revoked or unacknowledged active publisher retains a cleanup
   quarantine before the next grant; a missing callback cannot free it early.
3. Integrate through small Hub service ports and an additive negotiated
   assignment/response/control projection. The Worker executes one assigned
   permit and independently fences PCM using its deadline and fresh Hub
   state. No Worker creates a queue, chooses another speaker or issues a
   permit. Legacy envelopes remain closed; negotiated enforcement cannot
   silently fall back to unmanaged speech.
4. Bind headless interruption/barge-in to explicit Hub policy, exact current
   permit and task authority. Discard obsolete generation and PCM; an old
   callback, renewal or reordered control receipt cannot restore a permit.
   Silence, output duration, input wait and cleanup all have hard bounds.
5. Verify durable FIFO/priority/aging, independent-process races, scope
   substitution, expiry, restart and delayed completion first. Then verify
   closed contracts and Worker interruption, followed by actual two-Worker
   private Meet audio turn-taking with remote progress observations.

## Architecture and acceptance boundaries

The new repository owns resource state only (SRP); orchestration and policy
remain in Hub services with injected ports (DIP). Do not expand the existing
large dialog composition service into another scheduler. Its existing breadth
is preserved technical debt, not a reason to mix persistence into routes.
The existing GPU lease implementation is a reference, not a base class:
generation completion and actual speaker shutdown have different lifetimes.

A tested repository alone does not finish MAP-26. Completion requires the
negotiated production path and real two-Worker publication test. Synthetic
policies and test audio remain technical observations, not production release
evidence. No changes to the publicly serving Meet instance are part of this
private implementation slice.

## Durable resource verification

`SpeakerTurn` is an immutable, closed authority binding; `SqlMeetSpeakerFloor`
uses a database room-row lock and two resource tables, without changing the
Hub task queue or GPU admission. The room key covers physical origin/room,
not tenant: two separately authorized tenants in one room must not receive
two simultaneous permits. Exact task/tenant/project/runtime/dispatch scope is
still required to inspect or withdraw a reservation.

The first bounded policy has four waiting places, a ten-second wait, three
priority levels and three-second aging steps. At equal effective priority,
arrival order wins. Output authority is capped at sixty seconds and always
at the original source/task deadline. Revocation and natural expiry retain
four seconds of cleanup quarantine. Polls and repeated callbacks never
extend or revive a permit. Longer occupied rooms produce a bounded admission
failure, not indefinite waiting or permission to overlap.

47 SQL/model checks passed in 38.54 seconds, including two real separately
spawned processes, independent database connections, restart, source-field
substitution, priority aging, replay, ambiguous numeric values, deadline and
lost-completion quarantine. The first process test failed because the broad
application fixture inserted another dependency's `tests` package ahead of
this checkout; the test now explicitly selects this checkout in fresh child
interpreters, matching existing dialog-start process tests. No SQL lock or
policy bound was relaxed. Log: `/tmp/ananta-meet-speaker-floor-sql-final.log`.

This establishes the resource primitive only. Production composition, signed
Worker handoff, current-control projection and actual audio-floor acceptance
remain required before MAP-26 is complete.

The separate `MeetSpeakerFloor` service uses an injected persistence port and
current-authority checks before, throughout and after bounded admission.
Monotonic waiting remains limited if the wall clock moves backwards. Explicit
Hub policy checks surround headless interruption; withdrawal is terminal and
never selects a replacement Worker. Eight service tests passed in 12.08 s
over the real SQL repository (`/tmp/ananta-meet-speaker-admission.log`). These
are resource-service checks, not yet live output or API activation.

The additive wire contract negotiates `speaker_floor: true` in a Hub speech
assignment. Only this mode accepts/requires a speaker permit in a generated
spoken reply; the legacy reply remains closed and rejects the additional
field. The permit is covered by the existing request-bound reply signature
and cannot outlive the ordinary speech binding. An optional, closed
`speech_finished` field on control exchange carries that same permit; Hub
integration must additionally require negotiation and exact current ownership.
74 contract/legacy transport tests passed in 36.33 s, including expiry,
unnegotiated/missing permit, numeric ambiguity and foreign-action completion.
Log: `/tmp/ananta-meet-speaker-contract.log`. Runtime activation is still pending.
