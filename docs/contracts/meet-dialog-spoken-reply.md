# Spoken dialog result handoff — implementation plan

MAP-22 follow-up after actual local Piper-to-Meet transport was verified.
This document is a plan, not an active endpoint or runtime capability claim.

## Decision

Use a separate bounded authenticated spoken-reply request/response rather than
a durable PCM outbox. Generation already executes as a Hub-owned child media
task. The Hub can project its completed result into that one response, avoiding
new shared filesystem assumptions, persistent media retention, a streaming
store/queue and Worker-to-Worker dispatch. One response may hold at most the
existing 40-second PCM/WAV budget; no unbounded live stream is introduced.

Keep the existing `/internal/dialog` request, 16-KiB control response, schemas
and text-only chat action unchanged. A distinct `/internal/dialog/speech` path
will accept a closed chat-input request and return its own versioned envelope
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
