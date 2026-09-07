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

## Guard planned before implementation

Add a bounded AST regression detector for this standalone Python Worker package:
reject Hub `agent.*` imports (including literal dynamic imports) and explicit
Hub task-ingestion/delegation calls. The CLI must parse, never execute, source;
report only path/line/fixed rule codes. Keep it independent of Flask/GPU.
Exercise aliases, comments/strings, async contexts, malformed source and the
actual package. This is a narrow accidental-coupling detector, not a sandbox,
network firewall or proof against deliberately obfuscated malicious code.

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
