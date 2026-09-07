# Live spoken-dialog interruption — next bounded slice

Status: planned, not a completed acceptance claim. Tracks MAP-12/22/24/26 and
the companion MDS-10. The short real GPU dialog now proves ordinary speech,
renewed consent and text-only operation after speech pause, but it pauses only
after the preceding speech has finished. That does not prove mid-output stop.

## Existing implementation to preserve

`DialogSpeechOutput` already rechecks current Hub controls and exact assignment,
Meet lease, consent and source revisions, clears the owned PCM/publication on
revocation, and does not reopen a consumed answer. `DialogChatPump` admits no
stale result after a changed activation. The Hub's existing task-control CAS
remains authoritative; tests must not rewrite Worker state to simulate a stop.

The existing unit matrix covers changed speech/chat/receive/membership/lease,
grant removal, expiry, navigation and stale browser authority. The actual
browser source has its own bounded watchdog and generation-conditional cleanup.
No architecture change, new worker scheduler or worker-to-worker task is needed.

## Real-browser acceptance

Use the existing private, cryptographically admitted Hub/Worker/Meet fixture,
with explicitly synthetic non-silent PCM long enough to interrupt deterministically.
The real Qwen/Piper/NVENC transport has separate current-source verification;
a deterministic tone must never be labeled model or voice-quality evidence.

1. Wait for measured partial local progress and actual non-silent receiver audio,
   with substantial samples still pending. Do not use a guessed sleep to assume
   that speech is running.
2. Issue speech-only pause through the real Hub control path and its current CAS
   revision. Require owned local source closure within three seconds of the
   completed Hub mutation, partial (not complete) sample accounting and cleared
   pending PCM. Do not loosen the existing 200-ms queue or refresh interval.
3. Require the remote publication to disappear within four seconds. Independently
   check that the screen remains operational, the room stays joined and a new
   admitted chat input gets a text-only answer. No old speech may reopen without
   a new input and explicit current speech authorization.
4. Separately cancel the parent task while speech is running. Verify bounded
   Worker termination, remote departure and owned-resource cleanup; no old
   callback may recreate a publication under another generation.

Checks and policy changes are fully headless. Only the test's own temporary
identities, room, media and containers are in scope. Missing readiness and stop
deadline violations must fail with bounded, content-free diagnostics.

## Structure and reporting

Keep live progress observation and interruption scenarios in small test helpers,
separate from GPU provisioning, Hub authority and production publication code.
The existing large cross-repository fixture is an acknowledged SRP debt; extract
small reusable pieces as needed, without a big-bang fixture rewrite.

Report measured stop delays, sample/packet counters and classifications only.
Do not persist chat, PCM, media keys, grants or browser profiles. This slice
does not prove generative barge-in policy, multi-agent fairness, continuous
avatar lip sync, public TURN, hours-long GPU soak or production release evidence.
