# MAP-07 room assignment and minimal participant audit

This audit concerns the four existing MAP-07 criteria, not production release
evidence, trust deployment, multi-agent meetings or completion of the full MAP
track. All local identities and policies remain explicitly synthetic.

| Existing criterion | Implemented boundary and verification |
| --- | --- |
| Hub creates/joins an authorized own room; an invite alone is not administrative authority | `MeetRoomAllocation` requires a separate exact operator scope policy and current project/task write access. It prepares a random unbound association through SQL CAS. The normal `MeetDialogService.start` creates a Hub task and a freshly scoped signed grant; Meet independently admits the machine. The private gate confirms the allocated target is initially empty and that the first actual participant is the machine. `RoomRegistry.join` creates the ephemeral room only at that authorized join. |
| Create/join/leave use existing ports and reusable project/task bindings; manual flow remains compatible | Allocation depends on `RoomBindingPort`, existing `MeetBindingService` and `SqlMeetingStore`; current-revision reuse, manual attachments, tombstones, concurrent changes and task isolation are tested. Actual joining uses the existing Worker HTTP assignment and `DialogSessionOperations`. The Hub stop closes the actual participant. No Worker task queue or second scheduler is added. |
| First agent is visibly KI and has no implicit audio/video/screen source | The gate grants only `chat.send`, with chat/audio off. Before the human enters the new room, Meet has exactly one machine and zero publications. After a normal authenticated human join, the browser displays the exact `Ananta (KI)` heading, still with zero machine publications and zero human capture calls. Four successful Hub control exchanges are observed; the media executor is never called. |
| Human identity or avatar does not confer moderation/room administration | The machine admission fixes the label and uses its own issuer/device/session binding. Meet's `machineMessageAllowed` is enforced before message handlers even for a first-room creator; machine admission rejects human/pair/name/scope upgrades and recording/tool capabilities. Allocation returns the unchanged false membership/existence flags and cannot submit an avatar, privilege flag or arbitrary URL. The corresponding negative paths and manual compatibility are covered by existing admission/binding tests. |

The private single-host gate uses separate sandboxed Chromium, TLS and STUN test
containers, actual Hub SQL/task/CAS and signed HTTP callbacks. Its application
test processes are host-side fixture adapters; it is not a deployed multi-Hub
or multiple-worker-volume crash test. It does not use GPU inference, public
TURN, production credentials or the running `webrtc.ananta.de` instance.

The initial attempt was rejected by the current-build preflight in 8.97 seconds
before provisioning: concurrent Meet work had made the previous bundle stale.
A new private Angular build at Meet `4d40b45` completed in 7.438 seconds without
touching serving assets. The first real gate passed in 25.24 seconds, with
1,193.69-ms observed removal. The final visible-label gate passed in **20.79
seconds**, with four validated Hub exchanges and **1,146.63-ms** removal.
See [allocation contract](meet-headless-room-assignment.md) and
[private gate plan](meet-silent-allocated-room-gate.md) for boundaries.

The final twelve-file room/allocation/HTTP/SQL/recovery/dispatch/fixture
regression passed **193 tests in 74.44 seconds**. Ruff, Node syntax and TODO
consistency checks passed. MAP-07 is therefore `done/100` for its four existing
criteria; historical API-only partial notes remain explicitly marked as history.

SRP/ISP/DIP: allocation policy/service, HTTP parsing, SQL binding persistence,
private stdio observation, test infrastructure and the short scenario are
separate. The existing task service and Worker runtime remain composition
points; the large previous media scenario was not extended with another branch.
Test-only launch adapters and existing global Hub database composition remain
explicit fixture dependencies, not new production orchestration.

MAP-05/06/08 retain their separate complete trust/identity/encryption criteria;
MAP-11/20/22/24/28/30/31 retain broader recovery, live video, selected-voice GPU,
fairness, multiple-agent, public and long-duration criteria. Closing MAP-07 for
the above four verified criteria does not close or bypass any of those gates.
