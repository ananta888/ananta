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
