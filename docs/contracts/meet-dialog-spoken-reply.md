# Spoken dialog result handoff — implementation plan

MAP-22 follow-up after actual local Piper-to-Meet transport was verified.
The closed envelope, shared WAV validator, optional independent Hub speech
control, private Hub HTTP endpoint, bounded Worker transport and continuous
chat/speech runtime composition are implemented and tested locally. Actual GPU
inference behind this complete Hub/dialog path remains open; no deployment or
production release is claimed.

## Decision

Use a separate bounded authenticated spoken-reply request/response rather than
a durable PCM outbox. Generation already executes as a Hub-owned child media
task. The Hub can project its completed result into that one response, avoiding
new shared filesystem assumptions, persistent media retention, a streaming
store/queue and Worker-to-Worker dispatch. One response may hold at most the
existing 40-second PCM/WAV budget; no unbounded live stream is introduced.

Keep the existing `/internal/dialog` request, 16-KiB control response, schemas
and text-only chat action unchanged. A distinct `/internal/dialog/speech` path
accepts a closed chat-input request and returns its own versioned envelope
under a separate HMAC domain. Responses are limited to 2,700,000 bytes, sufficient
for the existing at-most-2,000,000-byte WAV and bounded metadata, never MP4.
The response signature binds the exact request and response; replay/admission,
current Hub task authority, current Meet membership/lease and input consent
remain mandatory. No new capability is enabled merely by adding this contract.

## Incremental implementation

1. Closed request/response contract, exact WAV/voice-profile/sample validation,
   and negative tests for unknown/duplicate fields, oversized media, mutated
   scopes and bad signatures. Metadata includes parent runtime/task/dispatch,
   Meet lease generation/membership/control binding, input correlation and the
   actual Hub-issued child generation task/lease; no invented evidence identity.
2. Reuse current chat admission and reply generation through small authority and
   result-projection ports. Speech requires explicit `speech.publish` as well
   as current chat input/reply rights. Recheck authority after generation and
   before response release. No media goes into task metadata or a new store.
3. Bounded Worker response reader and PCM decoder. Only after authenticated
   validation and fresh Hub/browser control checks may the task-owned speech
   sink consume the PCM. Old lease/control/input revisions discard the entire
   pending reply; there is no automatic reopen or alternate provider.
4. Compose with the dialog's existing current-state loop and independently
   stoppable speech source. Preserve text-only compatibility. Add separate
   speech-control projection before claiming independent Hub pause/resume.
5. Headless real HTTP races/limits and actual Hub + Piper + Meet integration,
   including renewal, consent revocation, cancellation and completion cleanup.

The existing worker/browser process deadline remains necessary if JavaScript or
inference stalls. Local consumed samples remain distinct from receiver delivery
and production release evidence. Unit doubles, synthetic Hub admission and
separate component probes must not be reported as completing step 5.

## Contract verification

`ananta_contracts/meet_spoken_reply.py` separates request/response HMAC domains
from each other and from the existing dialog protocol. The closed response
binds parent IDs, sender/input correlation, Meet session/membership, independent
receive/chat/speech revisions, deadline and actual child task/lease IDs.
`meet_speech_audio.py` verifies and decodes the bounded WAV under the pinned
voice profile; the existing Hub validator reuses it without changing its error
API or optional-profile compatibility. No PCM/text appears in the decoded
reply's representation.

143 contract, speech-profile/binding, existing dialog-transport and media-turn
tests passed in 95.07 s on 2026-09-07; Ruff and diff checks passed. These are
technical regression tests. There is no new route, caller authority or
production release evidence from this contract-only step.

## Independent speech control

Explicitly `speech.publish`-assigned new tasks receive an optional `speech`
source control, with its own enabled/revision/since values. The Hub rejects a
speech control on an unassigned task, even when its enabled value is false.
No UI/control operation can add the capability. Existing tasks retain precisely
the original three-source projection; an older three-source update preserves a
present speech control rather than implicitly enabling or disabling it.

Speech pause/resume updates its own source revision while keeping chat, audio
receive and screen revisions intact. Parent updates retain ordinary TaskQueue
compare-and-set semantics. The Angular panel accepts and displays this optional
control only when backed by the assigned capability; it does not yet offer a
new speech-enabled start flow before runtime composition is complete.

47 backend regressions passed in 37.48 s, followed by 14 controls/real task-CAS
and actual legacy Hub/Worker/Meet browser checks in 41.05 s. The existing chat/
screen loop still passes with the unchanged three-source shape. 49 Angular Meet
tests, feature lint and template/type checking passed; the unrelated existing
KnowledgeHygiene RouterLink warning remains. Updated Python modules are
formatted/linted; Hub/Worker responsibilities and task ownership are unchanged.

## Hub result projection

`MeetDialogSpokenReply` composes the existing SQL-backed chat reservation and
Hub-owned child generation service. `CurrentDialogChatAuthority` was extracted
unchanged from the dialog service; a separate speech authority adapter adds the
explicit capability, activation timestamp and independent revision checks.
This keeps admission policy and result projection separate (SRP) without a
second scheduling or persistence path (DIP/composition).

The private `/api/meet/v1/internal/dialog/speech` route authenticates the small
closed request in its own HMAC domain and signs the bounded response. Only
correlated text, WAV/profile/sample receipt and exact parent/child bindings are
projected; generated MP4, grants and source task metadata are not returned.
Authority is rechecked after generation and again after WAV validation. Speech
revision is included in the existing durable reservation/dispatch fence, so
revocation or pause/resume cannot release a stale result or retry the consumed
input. Pausing speech during a pending spoken reply discards that entire reply,
including its text; it does not convert that reservation into a text retry.
Later new inputs may still use the independent ordinary text path.

57 focused service, route, SQL admission/dispatch and existing text/dialog
regressions passed in 43.27 s on 2026-09-07. They include source revocation,
chat/speech revision changes during generation, stale membership/lease,
cancelled tasks, wrong profiles/child bindings, malformed input, replay and
oversized responses. New service/test modules pass Ruff; the existing compact
route module was changed narrowly, not globally reformatted. These are local
synthetic technical checks, not actual Hub/Piper/browser integration or
production release evidence.

## Worker transport

`HubSpeechClient` derives only the `/speech` suffix from the fixed operator
dialog endpoint. It cannot select a provider or delegate a Worker. Proxy
environment variables and redirects are disabled. A separate method on the
existing callback client leaves all old actions and their 16-KiB reader intact.
The speech reader caps bytes, rejects compressed/oversized/incomplete responses,
authenticates exact request-bound bytes before parsing/decoding and compares
the closed reply with the caller's current authority projection. Network reads
have bounded timeouts and an absolute budget check; a read already blocked in
the socket can consume its remaining socket timeout before that check runs.
Timeout, invalid signatures, stale scope or invalid media return one redacted
failure, with no redirect, retry, text fallback or source reopen.

80 transport/contract tests passed in 56.23 s, including 22 Worker tests with
real loopback HTTP for valid PCM, invalid signatures/domains/request binding,
scope changes, oversized/truncated bodies, redirects, deadlines and request
preflight. New modules pass Ruff. This tests transport, not yet productive
continuous playback or actual GPU inference behind this Hub endpoint.

## Continuous runtime composition

`DialogSpeechOutput` owns only the current PCM publication and its current
authority checkpoint. `DialogChatPump` still uses its one existing Hub request
pool; it selects the speech callback only for explicitly enabled assigned
speech. No new inference thread, task queue, provider routing or capture path
was added. The old combined chat completion block was extracted into a separate
method; media publication stays in its own adapter (SRP/ISP/DIP).

A finished spoken request cannot immediately publish: the runtime first obtains
a newer Hub exchange, then checks the exact pending receive/chat/speech and
lease bindings. An equal-looking reopened browser chat queue is not the old
activation. Correlated text is reserved in the browser before any PCM is pushed;
failed correlation closes the owned speech generation, with no text/audio retry.
Speech pause leaves future ordinary chat replies available. Revocation, task
cancellation, renewal, changed membership, stale cached state and source failure
close only the owned generation and drop its PCM references. Immutable Python
bytes are not claimed to be securely zeroized.

Hub state is refreshed by the existing two-second exchange and may be used for
at most 2.5 seconds after receipt. Browser consent/lease watchdogs remain active.
Repeated authority checkpoints within a frame share a browser observation for
at most 50 ms; all local Hub checks still run, and every browser PCM push/status
independently revalidates the publication lease. While speaking, the controller
uses a 20-ms tick, with PCM serviced before screen capture. The 4410-sample /
200-ms queue and strict no-underflow policy are unchanged.

The first real speech integration exposed queue starvation: redundant browser
RPCs took 316 ms to submit part of a frame batch. Sharing the narrowly bounded
browser observation reduced RPC cost, but the old 100-ms idle cadence still
starved the queue. The active 20-ms cadence fixed the reproduced failure without
increasing buffering or weakening authority. 26 dedicated output/race tests
passed in 22.84 s, and the final text + speech real Hub/Worker/Meet browser gate
passed in 71.36 s. The speech variant completes two exact local 22050-sample
outputs (the second alongside screen sharing), replaces input consent, pauses
speech through the Hub and receives a third text-only answer before stopping.
These are synthetic model/WAV fixtures, not decoded non-silent GPU delivery.

One preceding combined run had 50 passes and a private fixture machine-page
startup timeout before the speech dialog started; that failed run remains a
failed observation, not counted as acceptance. The subsequent two-browser-case
run above passed. The large pre-existing integration fixture still coordinates
several infrastructure lifecycles; new speech observations were extracted into
`tests/meet_dialog_speech_observer.py` rather than expanding that responsibility.
Ruff and diff checks pass. Remaining work includes the combined real GPU path,
live mid-output revocation/load/soak acceptance and the speech-enabled UI start.
