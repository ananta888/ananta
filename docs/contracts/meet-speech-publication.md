# Generation-bound PCM publication adapter

MAP-22 has a Worker-side sink for the independent Meet `speech` source
(initial companion implementation `ef65c8b`). It is now wired into the continuous
dialog through an explicitly authorized `speech.publish` capability, separate
signed spoken-result transfer and current voice/control binding. The original
small text callback remains unchanged. No deployment or public readiness claim
follows from this adapter; the foundation observations below remain historical.

Current production dialog delivery now uses
[Hub-fenced browser-local PCM feeding](meet-browser-pcm-feeding.md), after the
same receipt validation and nonblocking opening. `SpeechPublication` remains
the synchronous/injected compatibility producer and standalone real-Piper sink.
The limits and historical producer behavior below describe that retained port,
not a requirement to feed production PCM through Python RPCs every 20 ms.

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

The opt-in `tests/test_meet_speech_cross_repository.py` now connects actual
Piper/CUDA output from a separate current-source, read-only, networkless GPU
container through `SpeechPublication` to the companion's private stdio fixture
and required-SFrame receiver. On 2026-09-07 both browser variants passed in
29.52 seconds: Chromium consumed 44,544 local samples with 79 non-silent remote
observation windows; Firefox consumed 41,216 with 75 non-silent windows. Each
uses a separate real synthesis of the fixed German test phrase, so sample
counts need not be identical. Both recorded zero capture and transform errors.
No generated PCM is included in the report. The test bridge has synthetic,
cryptographically verified Hub admission, **not the productive Hub callback**.

Run with `MEET_SPEECH_CROSS_GATE=1 .venv/bin/python -m pytest
tests/test_meet_speech_cross_repository.py -n 0`; build current companion source
first. This requires the existing local GPU model/image and private TLS/STUN
fixture images, not production credentials or changes to running services.
For an XML report with `record_property`, use `-o junit_family=legacy`.

Subsequently implemented: separate Hub-owned, short-lived result transfer bound
to generation, parent runtime, child task, dispatch lease and current input
consent; independent runtime speech controls; source-stop tests; and real default
Piper delivery through the dialog. See [live avatar verification](meet-live-persona-avatar.md),
[voice-selection history](meet-voice-preset-selection.md),
[nonblocking source opening](meet-dialog-speech-opening.md) and
[delayed-screen regression](meet-dialog-screen-decoding.md). The selected-voice
GPU rerun, broader shared-load/soak and public acceptance remain open. Full WAV/PCM
does not enter the existing 16-KiB dialog callback, and a browser receipt is never
Hub permission.

SRP/ISP/DIP review: browser mechanics, closed wire validation, PCM framing and
publication state are separate; no model or concrete browser SDK is imported by
the publication state machine. Existing dialog-loop responsibility debt is not
expanded by introducing an unverified speech branch.
