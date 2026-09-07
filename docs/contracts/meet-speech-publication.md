# Generation-bound PCM publication adapter

MAP-22 now has a Worker-side sink for the independent Meet `speech` source
(companion implementation `ef65c8b`). It is a building block, **not yet wired into
the continuous dialog runtime**. That runtime still rejects `speech.publish`;
its authenticated reply callback still returns text only. No new policy,
deployment or public readiness claim follows from this adapter.

## Boundaries

- `ananta_contracts/meet_speech_source.py` validates the exact versioned source
  receipt and progress projection. Neither is an authorization or evidence ID.
- `SpeechPublication` accepts already delegated `SpeechFrame` values, a known
  total sample count, and a mandatory current-authority callback. It owns one
  local publication generation, not the Hub queue or speaker decision.
- `BrowserSpeechPort` implements only open/status/push/generation-conditional
  close on the task-owned page. Setup is started without awaiting its Promise
  in a blocking Python RPC; it polls every 50 ms with a ten-second deadline,
  navigation checks and mandatory authority checkpoints. Late setup completion
  cannot replace a newer operation or close its source.

The operation token used for local Promise fencing is not an `SRC_*`/`RUN_*`
identifier and grants no authority. No text/model/URL/grant is accepted by this
PCM port. The page remains responsible for verified Meet session rights and
required-SFrame protection. The caller must supply the actual Hub lease/control
checkpoint and retain the existing isolated browser process watchdog; a browser
whose entire JavaScript thread stalls still needs external process termination.

## Bounded behavior

The sole format is PCM16LE, 22050 Hz, mono. Frames contain 441 samples, except
the exact final remainder. `push(frame)` returns false without advancing the
sample counter when the 4410-sample browser queue is full; the caller retains
that same frame until writable. There is no retry scheduler, producer thread,
extra PCM queue, resampling, padding or implicit source reopen.

Receipts must match the exact source, total samples, version, queue size and
format. Progress must match this generation, sent count and monotonic consumed
count. Completion requires all declared samples; the browser increments its
generation on completion. A different active generation, malformed status,
expired wall/monotonic deadline, clock rollback or revoked authority closes only
the owned source. Checkpoints also run after status and after each write, so a
revocation observed during an operation immediately triggers cleanup.

Local `played`/completion is **not** remote sample-delivery acknowledgment.
Already transmitted media cannot be recalled; immutable Python frame bytes are
not claimed to be securely zeroized. The Worklet owns its bounded discardable
queue independently. A provider and any future Hub transfer must retain their
own bounded memory, lease and cleanup contracts.

## Verification and remaining integration

`tests/test_meet_speech_publication.py` exercises exact PCM, queue pressure,
partial final frames, stale generations, closed/malformed receipts, authority
races and deadlines. `tests/test_meet_speech_browser.py` tests unresolved Promise
cancellation and executes the exact JavaScript phase functions with controlled
Promises. These are deterministic technical tests, not real TTS/Meet evidence.
The companion repository separately tests actual decrypted audio in Chromium
and Firefox, including reopening and renewal.

Still required: Hub-owned, short-lived result transfer bound to generation,
parent runtime, child task, dispatch lease and current input consent; real Piper
output through that path; a runtime policy/control for speech and integrated
source-stop/load tests. Do not put full WAV/PCM into the existing 16-KiB dialog
callback, invent a Worker-to-Worker dispatch, or reinterpret a browser receipt
as Hub permission.

SRP/ISP/DIP review: browser mechanics, closed wire validation, PCM framing and
publication state are separate; no model or concrete browser SDK is imported by
the publication state machine. Existing dialog-loop responsibility debt is not
expanded by introducing an unverified speech branch.
