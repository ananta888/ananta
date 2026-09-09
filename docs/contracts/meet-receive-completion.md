# Receive completion after source audit

## Source audit: 2026-09-09, Ananta `26f3a47c7`

MAP-25's older note predates the actual MDS audio adapter and normal Hub
`meet_audio_receive` child Tasks. These now exist in `dialog_audio.py` on each
side of the boundary. The Worker opens one authorized publication, preserves
sample offsets, acknowledges bounded batches, runs local pinned ASR, and sends
an ephemeral transcript through the exact Hub child assignment. The Hub owns
admission and any subsequent reply Task. No raw audio, SFrame key or transcript
is persisted by that path. Source, membership, receive and control revisions
are checked repeatedly. Session renewal closes the current audio window.

Remaining work is real, rather than wholesale absence of the audio path:

1. Remove the unnecessary `chat.send` requirement for **transcribe-only**
   sessions. Share one pure audio-mode capability predicate across request,
   persisted authority, signed Worker assignment and child admission/current
   checks. Dialog replies still require send permission and enabled chat policy.
   Sending alone must never imply receiving; revoking required rights terminates
   the assignment. Exercise the complete capability matrix automatically.
2. Introduce a closed, explicitly selected local receive profile for bounded
   language, installed pinned ASR model, segmentation and VAD options. Preserve
   the legacy fixed ten-second profile when no new option is supplied. Bind the
   profile to the Hub Task/assignment and current authority; accept only its
   permitted sample range and language. No path, arbitrary model/provider,
   download or cloud fallback comes from meeting content or Worker preferences.
3. Separate batch validation/segment closure from browser lifecycle and ASR
   supervision. The existing large `DialogAudioPump` mixes these concerns (SRP
   debt); narrow injected execution/segment ports will keep cancellation and
   backpressure independently testable (DIP/ISP).
4. Add separately granted visual reception in both repositories, default off,
   with publication/source/epoch/session bindings and bounded decoded frames.
   Delegate analysis through ordinary Hub-owned Tasks; no Worker routing or
   shared raw-media relay. Any supported local analysis profile must be explicit,
   and output cannot grant tools, publication, retention or training rights.
5. Verify repeated segments, renewal, revoke, backpressure, crash/cancellation,
   buffer erasure and actual installed GPU/native reception headlessly. Check
   the companion source and public instance without changing operator trust or
   granting production receive policy. Synthetic observations remain distinct
   from Hub-registered production evidence.

MAP-25 stays open until all acceptance criteria, including visual analysis,
have been implemented and verified. Small reviewed commits are checkpoints,
not completion of the overall 32-task request.

## Receive-only capability separation

The shared pure `audio_mode_permitted` predicate now guards Hub start,
persisted current authority, signed Worker assignment and audio child
admission/revalidation. Transcribe-only requires `audio.receive` and no chat
publication right. Dialog still requires both `audio.receive` and `chat.send`,
plus non-off Hub reply policy. Removing either required capability or disabling
dialog reply policy invalidates persisted authority; no privilege is inferred
from sending or from local model availability.

The focused headless selection passed 143 tests in 56.67 seconds, including
all nonempty combinations of four receive/chat/speech capabilities, invalid
modes, actual SQL Hub Task creation, signed assignment validation, ephemeral
receive-only child completion and revocation. Existing audio/task/transport/
speech-control regressions remained green. The standalone boundary check passed
all 108 Worker files. This fixes duplicated policy coupling (DIP/ISP); the
larger browser-pump SRP extraction and remaining receive features are still open.

## Closed audio profiles and fixed segment execution

An optional immutable `audio_profile` now binds explicit `de`/`en`, the installed
`whisper-small-pinned` model, local VAD on/off and a 1–10 second segment. Hub
request/current authority, original preauthorization digest, dispatch router,
signed Worker assignment, child Task and native ASR child preserve the same
projection. Global callback bounds are not authority: Hub completion requires
the exact delegated sample count and language. Legacy assignments omit the new
field and retain their previous ten-second behavior.

`AudioBatchCursor` extracts bounded batch/timeline validation from browser and
ASR lifecycle (SRP/DIP). It accepts at most five canonical chunks at once, never
retains their PCM, advances only after successful ACK and rejects truncation,
oversize and replay. Closing the pump clears its pending Future reference,
cancels work and wipes the existing receiver buffers. A transcribe-only Worker
also rejects an unexpected reply rather than calling the publication port.
The Hub composition methods remain relatively large existing SRP debt; a small
profile/error adapter prevents duplicating validation or adding more branches.

Initial focused checks: 64 profile/SQL Task/router/preauthorization/batch cases
passed in 30.58 seconds and 212 legacy regressions in 77.11 seconds. The expanded
execution selection passed 80 tests in 37.68 seconds, including real bounded
PCM buffering and exact two-second completion, cancellation/wipe and no reply
publication. Two initial failures were a test inspecting the decoder's
nonexistent public `limits` attribute; the assertion now inspects its actual
configured `_limits`, without altering decoder behavior. Native pinned-image
RTX3080 profile probes are the next verification step, not yet a live-receive
or production release claim. Early VAD segmentation and visual analysis remain
open parts of MAP-25.

The packaged RTX3080 probe subsequently passed both VAD-on and VAD-off cases
in 26.78 seconds using committed source `730bdda73` and immutable image
`sha256:bb558478ecf3b573a04938338714862b16eda8323df781aa386bd473b487a452`.
Each used a separate non-root read-only network-disabled container, mounted only
the existing models and allowlisted GPU driver files read-only, recognized the
synthetic phrase from exactly 64,000 samples and removed its own container.
No source mount, service key/state, microphone, provider network, or serving
Worker restart was involved. This is real local CUDA execution, not yet live
Meet reception or production release evidence.

## Next additive segment endpoint

Source audit `730bdda73` / Meet `0915ad9`: the browser audio port can select a
1–10 second maximum, but cannot stop capture early while retaining the current
subscription for its authorized response. Add a separately probed
`sample-boundary-v1` finish port. Only the exact current subscription and its
last acknowledged sample boundary (minimum one second) may finish it. Stop the
graph, wipe all later queued/incoming samples, retain only existing bounded
authority/reply state; do not renew or grant anything. Required-SFrame and the
blind signaling-server boundary remain unchanged.

An optional `energy-v1` segment strategy will be selected in the Hub profile,
with fixed bounded energy/speech/silence thresholds in an interchangeable Worker
port. It keeps counters, not extra PCM/history. Preserve fixed segmentation by
default. The Worker first probes the required finish method before receiving,
then validates/ACKs contiguous chunks only until the selected boundary. The Hub
accepts a 100-ms aligned 1-second-to-profile-maximum result only for this
explicit strategy. No changed source/lease/profile may finish or publish. Cover
headless positive/negative cursor, endpoint, wipe, reply, early-silence and
maximum-window cases, then actual decoded browser audio. This remains part of
MAP-25, not a separate orchestration loop.

The optional strategy and sample-bound finish port are implemented. The Hub
profile selects `energy-v1` explicitly; fixed remains the unchanged default.
The Worker uses a small boundary port and fixed mean-absolute-amplitude threshold
600, 300-ms consecutive onset and 500-ms trailing silence, with one-second
minimum and original 1–10-second maximum. No extra PCM/history is retained by
the detector. It requires the closed browser probe before opening a source and
verifies the exact finish receipt before ASR. Unknown/changed finish receipts
cannot trigger a transcript callback. Hub completion accepts early 100-ms
boundaries only under the bound energy profile, never under fixed assignments.

127 profile/segment/legacy tests passed in 51.96 seconds. A further 48 execution
and endpoint cases passed in 26.54 seconds, including actual bounded PCM
buffering to exactly 1.1 seconds, no-voice maximum closure, missing browser
support, forged finish receipt and transient buffer cleanup. Companion actual
Chromium/Firefox receive-only sessions decoded and closed exactly 17,600 samples
with SFrame, send denial and revocation in 7.744 seconds combined. GPU profile
probes above remain separate from these browser tests; a joined live ASR receive
chain and visual analysis are not yet claimed.

Companion source `b23de52` then passed its isolated full check: 775 frontend
tests, 825 Node passes / zero failures / two explicit skips; Node phase 330.898
seconds. Build, Go unit/vet and static gates passed; fourteen external
infrastructure checks and the optional image scan remained explicit skips.
The detached check worktree did not overwrite the serving build.
