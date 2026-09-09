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

Worker output now uses the separate `SpeakerPermitGate`: authenticated PCM
needs an identical fresh Hub control permit, a bounded deadline and an unused
sequence. Expiry, withdrawal, changed permit or stale ordinary control closes
only the owned speech source. A retired sequence cannot reopen after an old
control projection. Browser-local playback receives the shorter floor deadline.
Only successful source cleanup hands off one content-free completion locally;
the ordinary signed control exchange repeats that exact completion idempotently,
without another Worker scheduler or blocking cleanup HTTP request.

43 output/runtime tests passed in 24.98 s; 37 real loopback speech-HTTP and
control-exchange tests passed in 23.05 s. A further combined 78-test run in
38.82 s covers signed control projection/completion, legacy read behavior and
the SQL owner projection. The Hub composition and actual two-speaker room
test are still pending; no live floor claim is made from these tests.

## Explicit Hub activation

`ANANTA_MEET_SPEAKER_FLOOR=1` now composes the durable resource and the dialog
coordinator in the ordinary Hub bootstrap. Default `0` leaves existing legacy
deployments unchanged; malformed values fail configuration. New speech tasks
automatically carry the negotiated field in their stored original context,
Worker assignment and immutable preauthorization digest. A required mode with
no coordinator, or an unnegotiated speech task on an enforcing Hub, fails
before model generation instead of publishing unmanaged audio. Negotiation
is rechecked during generation as well as in control exchange.

Upgrade every Hub and dialog Worker and drain legacy speech sessions before
enabling this policy across the deployment. Mixed old/new Hub software is not
a supported rolling-enforcement boundary: an old Hub cannot enforce a field
it has never implemented. The enforcing coordinator also has a four-second
monotonic startup quarantine, rejects legacy speech control reads, and retires
its old projections during that interval. Configuration changes do not mutate
already assigned immutable task permissions or silently migrate those tasks.

The Hub now projects only the exact owner's current permit, accepts only its
exact completion, withdraws on chat/speech control changes and voice selection,
and retains quarantine after completion. A late completion cannot stop the
next task's permit. The service delegates persistence and admission through
separate small ports; no SQL or scheduler was added to dialog routes. The
existing broad `MeetDialogService.start` remains SRP debt; pure negotiated-field
projection was extracted rather than increasing its complexity or lint limit.

100 native authority/SQL admission/old spoken-output/bootstrap checks passed
in 46.22 s (`/tmp/ananta-meet-speaker-hub-final.log`). This includes rollback of
the negotiated field during generation, startup denial before GPU work, exact
completion, current speech pause and immutable preauthorization. Automatic
role-priority interruption and real two-Worker audio acceptance remain next.
An additional 25 ordinary bootstrap, preauthorization, route and authority
regressions passed in 20.42 s (`/tmp/ananta-meet-speaker-composition.log`).

## Role priority and automatic interruption

`ANANTA_MEET_SPEAKER_POLICIES` is a closed list of at most 64 operator rules.
Each names `tenant_id`, `project_id`, `organization_id`, `role_slot_id`,
`policy_id`, positive `revision`, integer `priority` (0–2) and boolean
`barge_in`. Duplicate scopes, wildcards, ambiguous numbers and extra fields
are rejected. An example rule is:

```json
{"tenant_id":"example","project_id":"example","organization_id":"example-org","role_slot_id":"chair","policy_id":"chair-speech","revision":1,"priority":2,"barge_in":true}
```

Only the exact current verified Hub machine principal selects this rule.
Unlisted roles and legacy principals use priority 0 without automatic
interruption. The immutable rule digest and organization scope are persisted
with the resource reservation; neither is an evidence-registry identity.
The optional existing injected priority callback stays substitutable, but
cannot enable automatic interruption or claim a configured rule digest.

An already admitted new input with an explicit `barge_in` rule can retire
only a strictly lower-priority active permit in the same tenant, project and
organization. It still waits through four seconds of cleanup and normal
queue ordering. An ended waiter, equal priority, foreign organization or
ordinary priority without that rule cannot interrupt. A preempted input is
aborted, not retried or requeued; pending-turn aging is not a promise that
low-priority speech finishes despite explicitly authorized interruption.
Default FIFO mode never preempts. Original wait/output/task limits remain.

112 combined policy/SQL/service/native-dialog checks passed in 48.76 s;
15 configuration checks passed in 16.14 s, including malformed/duplicate JSON
before any store write. Logs: `/tmp/ananta-meet-speaker-priority.log` and
`/tmp/ananta-meet-speaker-policy-config.log`. The full Worker Dockerfile built
tracked source `82a2508e7` without source overlays, producing local image
`sha256:3592969d5c58f71053a3f613b3365949a7f4b20dbc04bf4e448d1036569c9318`.
Subsequent changes here affect only Hub policy and its tests. The private
two-Worker audio acceptance is the next required verification.

## Actual two-Worker acceptance

The private gate now starts two immutable, separately role-assigned Worker
containers from the image above, the ordinary Hub dialog/task path, and a
separate Meet receiver. Both publish their own moving screen and persona.
The receiver pins the two actual connections from decoded screens and samples
both audio receivers in the same callback every 20 ms, independently of chat
panel navigation. Missing connections, more than 250 ms without observation,
overlapping non-silent samples or a 60-second observation deadline fail closed.
The test neither grants itself a floor permit nor bypasses SFrame or captures
human media. Each input produces exactly one native correlated child task.

On 2026-09-09, using Hub `badb5ec5b` and companion `cfca893` plus the bounded
fixture corrections recorded with this acceptance:

| Private scenario | Test duration | Fresh samples | Sampled overlap | Longest sample gap | First voice | Preemption-to-last-audio |
|---|---:|---:|---:|---:|---:|---:|
| Default FIFO | 90.19 s | 1816 | 0 | 51 ms | 14,000 ms | not applicable |
| Explicit second-role barge-in | 93.15 s | 1647 | 0 | 38 ms | 13,780 ms of planned 20 s | 913 ms |

Both include real non-silent output from the second Worker, terminal completion
receipts, independent task/source shutdown and owned test-resource cleanup.
The second speaker in the interrupt case starts only after the four-second
Hub quarantine. This is sampled receiver evidence, not a claim of mathematically
continuous measurement or of sub-sample overlap detection. Logs and JUnit:
`/tmp/ananta-meet-speaker-fifo-staged.{log,xml}` and
`/tmp/ananta-meet-speaker-barge-in.{log,xml}`.

Three earlier FIFO attempts failed in the fixture. One diagnostic accessed a
missing delegated `worker` property; the scenario now preserves that interface
(LSP). Floor installation also waits for the two already-authorized screen DOM
nodes to render before pinning their connections. The final observed timeout
had 700 first-speaker and 79 second-speaker samples, zero overlap, a finished
first permit and a still-active second permit: the old 18-second combined wait
did not cover the existing 25-second Hub response budget plus source opening
and four-second playback. Separate bounded reply and playback phases now do.
No production admission, output, cleanup, security or resource limit changed.
The earlier unclassified failure is not presented as a diagnosed transport bug.

The receiver helper has 23 passing deterministic checks. The remaining MAP-26
criteria use existing native chat/ASR paths: default-off and mention/question/
room modes; immutable sender/event/session/turn identity; SQL deduplication,
rate/cooldown/token/character/session budgets; rejection of self/machine input;
bounded rejection of missing input; and rechecked authority before publication.
Current-profile multimodal isolation is separately documented in
`meet-multimodal-input-isolation.md`. Conversation text does not activate tools,
change roles or issue policy. No new Worker scheduling loop was introduced.

These are explicit synthetic-policy/PCM technical observations. They do not
replace MAP-24 live A/V timing, MAP-11 recovery, MAP-30 GPU quality, MAP-31 public
TURN/two-hour soak or MAP-32 registered release evidence. Serving infrastructure
and trust configuration were not changed for this acceptance.

The final targeted Python regression passed 304 tests in 136.50 seconds,
covering speaker SQL/admission/policy/bootstrap, signed contracts, Worker
output, native dialog integration, chat admission/replies/budgets and both
old/new multi-Worker fixture contracts. Log:
`/tmp/ananta-meet-floor-acceptance-regression.log`. Counts from earlier suites
overlap and are not additive defect counts. MAP-26 is complete against its
own criteria; the larger media track remains unfinished.
