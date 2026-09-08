# Local speech output and compatibility WAV adapter

The bounded media turn consumes real Piper output incrementally through
`SpeechSourcePort`. The existing compatibility response remains WAV plus MP4.
The continuous dialog now also uses a separate signed, assignment-bound spoken
reply path and the [generation-bound publication adapter](meet-speech-publication.md).
This is not voice-cloning permission or a production release claim. The sections
below describe the earlier output foundation; live wiring is documented in
[dialog runtime](meet-dialog-runtime.md) and
[voice selection](meet-voice-preset-selection.md).
The current dialog transfers its one already-authorized bounded PCM result to
the [Hub-fenced browser feeder](meet-browser-pcm-feeding.md); inference and the
standalone pull/WAV contracts below are unchanged.

## Responsibilities and reuse

- `piper_speech.py`: operator-configured local CUDA model, sentence inference,
  exact format and sample validation. No model URL from a participant, download,
  alternate provider or silent CPU fallback.
- `audio_output.py`: pull-based framing and duration budget, independent of
  ONNX/Piper, filesystem and publication. One consumer pull yields at most one
  frame. No producer thread, background queue or worker orchestration.
- `speech.py`: the existing WAV compatibility entry point, consuming this port.
  It creates only a new task-local output, cleans that output on failure, and
  never overwrites/deletes an existing file. Its two-argument API and result
  tuple remain supported; dependencies/checkpoints are optional keyword seams.

This separates previously mixed model-loading, inference and file concerns
(SRP/DIP), with a single-method synthesis interface (ISP). Existing
`voice_runtime.streaming` is an inbound ASR lifecycle, not an outbound TTS
provider. Its decoder remains reused by the ASR smoke; no private resampler is
copied into this output path. Speech adaptation/correction and consent services
remain Hub authorities; a transcript correction profile is not a TTS voice or
permission to clone a person. Dynamic voice/profile admission now has its own
separate Hub services and passive selectors. A real selected-voice GPU path
has now passed; see the dated measurements and retained failed attempts in
[voice selection](meet-voice-preset-selection.md).

## Exact format and limits

The provisioned German Piper voice produces mono, signed 16-bit little-endian
PCM at 22,050 Hz. Alternate sample rates are rejected, not silently resampled.
Frames contain 441 samples (exactly 20 ms), except the unpadded final remainder.
Sample offsets are contiguous across sentence boundaries; integer microsecond
timestamps derive from the absolute sample count, never accumulated rounded
durations. There is no wall-clock pacing in the WAV consumer.

Input is nonempty, NUL-free and at most 450 characters. The total maximum is
40 seconds (882,000 samples); callers can reduce this budget. Provider output
is checked before emitting an over-budget sentence. NaN, infinity, out-of-range,
non-float32 or malformed model output is rejected before PCM conversion. The
framer holds less than 40 ms of pending PCM plus the current bounded provider
sentence; it does not claim that neural inference itself uses only this memory.
Piper may allocate a sentence before yielding it. The existing isolated
`TurnExecutor` subprocess deadline and container limits remain necessary for
an inference call that stalls or allocates excessively.

Checkpoints run before/after provider pulls and before each frame. Closing or
failing the iterator closes its provider and drops pending output; immutable
Python bytes are not claimed to be securely zeroized. The existing persona
turn's Hub lease and all turns' original deadline are wired into the WAV path.
Previously emitted frames cannot be recalled: the independent publication sink
binds its exact session/publication generation and requires current authority
before sending and is now composed into the continuous dialog. Frames
themselves carry no grant, evidence identity or room context.
Current MP4 publication still has its independent Hub lease checks.

## Headless checks

`tests/test_meet_speech_output.py` covers split sentence boundaries, exact PCM,
pull backpressure, local revocation, budget rejection, provider errors, output
cleanup and safe refusal to overwrite a file. `tests/test_meet_speech_gpu.py`
is opt-in with `MEET_SPEECH_GPU_GATE=1` against the private provisioned worker.
It runs actual Piper CUDA, measures first-frame time including model loading,
checks sample continuity, and stops another iterator without human input.
The local checkpoint time is not a Hub-network stop SLA or proof of live
receiver interruption. Probe reports are synthetic technical observations,
never production release evidence.

Private RTX 3080 reference probe on 2026-09-06, worker image
`sha256:ba1e63b273bd47970c6fc7997984bcef70c263969880de04ceb1474a0f215a85`:
106 frames, 46,336 samples (2.101 seconds), first frame in 1377 ms including
model loading, local checkpoint cancellation in 19 ms, two executions in
2.19 seconds overall. No live-room publication or human capture occurred.

## MAP-22 acceptance audit, 2026-09-08

The four task criteria are implementation criteria, not the separate public
deployment, multi-agent, long-soak or Registry-backed release gates:

| Criterion | Current source and verification |
| --- | --- |
| Reuse and consent separation | The inbound `voice_runtime.streaming`/ASR ports remain distinct from outbound `SpeechSourcePort`; existing correction/consent metadata grants no cloning or TTS identity. Hub voice policy/profile admission is separate. |
| Replaceable TTS port and Hub-bound inputs | `audio_output.SpeechSourcePort` is one injectable synthesis method; `PiperSpeechSource` is the real local implementation. The closed speech profile pins voice/language/model bytes/format/budget. Hub voice projection and `CurrentDialogSpeechAuthority` recheck selected persona/source revisions before admission and output. |
| PCM source and bounded sample clock | Pull frames are mono PCM16LE/22050 Hz with contiguous absolute sample offsets and bounded duration. The independent speech publication port and browser-local feeder enforce generation/current-authority fencing and the unchanged 200-ms playback buffer. |
| Real permitted adapter and bounded failures | The private RTX 3080 runs exercise actual Qwen/Piper and non-silent remote audio, including neutral/whisper selection and revocation. CUDA/model/budget failures remain closed; no cloud or alternate CPU runtime is silently selected. |

The selected-voice case passed in 100.93 seconds, then the combined avatar/
selected-voice GPU regression passed both cases in 180.10 seconds. Exact
measurements, synthetic policy classification and the retained earlier failed
attempt are in the voice-selection contract. These satisfy the real TTS-path
criterion without pretending synthetic policy is production authorization.
Other tracks retain broader A/V quality, continuous ASR, external TURN,
multi-agent and long-soak obligations. The preserved broad browser-fixture
composition is SRP debt; the production synthesis, framing, publication and
Hub-policy responsibilities remain separated through small injected ports.

The final focused synthesis/profile/binding/publication/voice/diagnostic/runtime
inventory regression passed all 210 tests in 79.14 seconds, including the
explicit real installed-image inventory gate. MAP-22's four criteria are
therefore complete; this does not close the surrounding Meet TODO or broaden
the accepted runtime/production scope.
