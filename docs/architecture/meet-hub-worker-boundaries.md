# Meet control and execution boundaries (MAP-02)

## Source-grounded port inventory

The Hub owns task ingestion, policy, original deadlines, dispatch, persona
selection and terminal CAS. Meet independently owns machine admission, device
proof, membership, source permissions and E2EE transport authorization. A Worker
executes one closed assignment and calls only the Hub for renewed authority or
delegation of a subsequent reply; it does not submit jobs to another Worker.

| Responsibility | Existing narrow boundary |
| --- | --- |
| Room association | `MeetingStore` / `MeetBindingService`; `RoomBindingPort` / `MeetRoomAllocation` for separately opted-in headless allocation. Binding is never membership. |
| Participant admission | Hub `MeetMachineGrantIssuer` and `MeetAuthorizationClient`; Meet `MachineAdmission` and `MachineSessionLeases`. Private signing keys stay on the Hub. |
| Session lifecycle | `HubDialogTasks` and exact current `MeetDialogAuthority`; Worker `DialogSessionOperations` and `DialogControlExchange`. Browser phases never renew Hub task authority. |
| Owned media source | `OwnedDialogScreen`, `AvatarBrowserPort` and pull-based `SpeechSourcePort`. These own frames/PCM, not a task queue or source-approval policy. |
| Publication | `BrowserScreenFrames`, `DialogAvatarPresentation` / `DialogAvatarPump`, `BrowserSpeechPlayback` / `BrowserPcmFeeder`. Byte/frame delivery is bounded and fenced by exact current grants and source generations. |
| Subscription | `ReceiveBinding`, `MeetAudioReceiver` and `DialogAudioPump`; the Hub owns audio-child creation through `MeetDialogAudio` / `HubDialogTasks.claim_audio`. A browser receipt alone cannot create a Hub input task. |
| Persona resolution | `MeetPersonaProfiles`, `MeetAvatarProfiles`, `MeetPersonaVoiceProfiles` and format-specific image/video/voice services resolve and recheck current Hub profile pins; Worker decoders/renderers only consume closed admitted assets. |
| Audio/video generation | `SpeechSourcePort.synthesize`, `speech_frames`, profile-pinned `PiperSpeechSource`, frame-source callback into `encode_frames`, and format-specific inspectors. Generation and publication remain separate consumers; an output buffer is not publishing permission. |
| Delegated answer | `MediaTaskPort`, `MediaWorkerPort`, `ChatDispatchPort` and `MediaCapacityPort` in the Hub reply service. Browser callbacks enter Hub admission; the Hub alone reserves/dispatched model work and returns the bounded result. |
| Expiry recovery | `DialogDeadlineStore`, Hub `MeetDialogDeadlines` and existing task CAS. The lifecycle tick settles expired tasks; it grants no retry, renewal or Worker orchestration. |

These are additive ports around the existing hub–worker architecture, not a
new media scheduler. Local ASR/model/FFmpeg execution inside an assigned Worker
is task execution; calling a local configured model runtime is not delegation
to an independent Ananta task-owning Worker. Browser and generator can share
one explicitly configured Worker container or be separate Hub-selected workers;
neither can transfer task ownership to the other.

## Container and trust boundary

`docker/meet-media/Dockerfile` packages `worker/meet_media`, shared
`ananta_contracts` and `voice_runtime`, not the Hub `agent` package. Compose
preserves separate Hub/Worker containers, a private Worker state directory,
no host profile/display/Docker socket, read-only keys and explicit media opt-ins.
The Hub-to-Worker HTTP assignment and callback response are direction/request
bound; persistent dispatch fences and original deadlines do not depend on a
mutable browser UI state. Synthetic tests are never production evidence.

Meet's server and native blind transport path do not perform model inference,
persona resolution or media decoding. Browser media keys remain in admitted
endpoints. The optional legacy decode/re-encode relay is explicitly separate
from required-SFrame blind transport; no universal blindness claim is made.
Cross-repository compatibility tests keep Human/Pair behavior distinct and
must not treat skipped public TURN/infrastructure checks as a successful gate.

## Implemented regression guard

`python scripts/check_meet_worker_boundaries.py` parses the standalone Python
Worker package and rejects Hub `agent.*` imports (including literal dynamic
imports) and explicit Hub task-ingestion/delegation calls. It never executes
audited source and reports only relative path/line/fixed rule codes, independently
of Flask/GPU. Input count and bytes are bounded; missing, linked, oversized or
unparseable source fails. Tests cover aliases, comments/strings, async contexts,
malformed source and the actual package. This is a narrow accidental-coupling detector, not a sandbox,
network firewall or proof against deliberately obfuscated malicious code.

## Acceptance audit (2026-09-08)

All four MAP-02 specification criteria are covered by the inventory and container
boundary above. The actual package scan passed for 77 Python files; 35 focused
architecture/reply-budget/transport/replay tests passed in 23.08 seconds.
Companion source inspected at `4d40b45`: `src/server.js` rejects binary signaling
and applies `machineMessageAllowed` plus current session leases before forwarding
messages. `media-edge-agent/media_agent.go` reads bounded RTP packets and forwards
them through `WriteRTP`; `federation.go` likewise gates forwarding by current
negotiated route state. These paths do not resolve personas or run generators.
Their RTP rewriting is transport continuity, not decoding; the distinct legacy
relay and required-SFrame scope are documented in `docs/blind-media-edge-agent.md`
in that repository. This is a source/architecture audit, not production runtime
or cryptographic implementation certification.

## Preserved engineering debt and verification scope

`MeetDialogService` and the Worker runtime remain broad composition points;
`DialogExecutor` combines replay persistence, launching and slots (SRP/DIP debt).
Some Hub modules still import the pure legacy `worker.meet_media.contract`
surface; moving its authoritative definitions into `ananta_contracts` requires
a separate compatible facade migration. Shared schemas must not gain Hub
service imports merely to avoid this naming debt. New architecture checks do
not rewrite those execution paths or introduce wider interfaces.

Task-level closure of MAP-02 means these responsibilities/ports are specified
and checked; it does not close the independent runtime/GPU/multi-agent/public
acceptance tasks or assert that every renderer/profile variant is implemented.
