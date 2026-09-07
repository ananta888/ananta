# Spoken dialog result handoff — implementation plan

MAP-22 follow-up after actual local Piper-to-Meet transport was verified.
The closed envelope, shared WAV validator, optional independent Hub speech
control and private Hub HTTP endpoint are implemented. Worker transport and
runtime composition remain in progress; this is not a deployed runtime claim.

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
