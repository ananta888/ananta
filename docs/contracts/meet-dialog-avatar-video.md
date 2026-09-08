# Session-bound persona video selection (MAP-12 / MAP-20)

## Source audit and implementation plan

At root `35166c9b0`, active avatar selection supports only neutral/image modes;
normalized silent video assets and profile-bound clip execution already exist.
Meet's avatar source likewise supports only neutral/image artwork. A stored clip
or the separate one-shot MP4 publisher is not a working live profile switch.

Add explicit `avatar_videos: true` negotiation, requiring the existing avatar
image negotiation and `avatar.publish`. Existing assignments and envelopes stay
unchanged when absent; old Workers reject the unfamiliar field. The Hub pins
the negotiated ceiling in the Task/source profile/preauthorization digest.
A separate owner-only video-selection command performs current profile/asset
authorization and the existing avatar-only control CAS, without enabling a
paused source. Repeat behavior (`loop` / `hold_last`) is explicit, not chosen by
the Worker. Image/neutral switches remain available by their existing command.

Use a separate closed video-hydration endpoint and request/response HMAC domains,
bound to original task/dispatch/runtime, exact selected asset/profile digest,
Meet generation/epoch, avatar revision and deadline. Recheck before and after
reading admitted bytes. No URL, media bytes or private path enters Task state.
Missing/revoked video policy blocks only this source, never selects a substitute.

The Worker keeps a single hydration slot and consumes its result only under a
new authenticated Hub update. A format-specific adapter reuses the existing
avatar generation/pulse/stop lifecycle. Meet renders one hash-checked normalized
silent clip as bounded artwork beneath immutable KI labeling, never via human
capture or a new signaling media endpoint. It owns and releases its decoder,
blob URL and camera publication; independent speech/screen remain untouched.
Decoder support/dimensions/duration/errors fail closed, without another codec
or image fallback. Native browser decoding is not a hard decoder-memory sandbox;
Hub admission/Worker normalization and container budgets remain necessary.

Preserve SRP/DIP by keeping selection policy, byte hydration, transport and
browser rendering separate. `MeetDialogService` remains an existing broad
composition point; no new asset decoding or scheduling belongs there. Do not
generalize image-only contracts silently to authorize video for old sessions.

## Required verification

- Closed negotiation, wrong type/mode/scope/hash/revision, stale hydration,
  selection races, missing provider and legacy compatibility tests.
- Real SQL Task immutability/CAS and signed Hub-to-Worker transport checks.
- Deterministic browser decoder/cancellation/stale completion tests; unchanged
  2.5-second pulse watchdog, 30-second source lease and current key protection.
- Actual private receiver proves moving clip, image/video replacement,
  independent speech/screen, pause/revocation and zero human capture.
- Companion full check in a private build; no serving deployment, trust change
  or production evidence claim. MAP-20 stays open until all its criteria pass.

## Implemented slice and technical observations (2026-09-08)

The negotiated ceiling, Hub selection/CAS, separate signed hydration, Worker
single-slot presentation and passive Angular video picker are implemented.
The original image endpoint rejects video selections, and the generic Task write
guard prevents adding, stripping or coercing the original video ceiling. Silent
video profile resolution requires only the authorized video output; the existing
one-shot audio/video profile adapter still requires both outputs. No inference
or asset decoding was moved into the Hub coordinator.

Focused backend batches passed: 172 contract/legacy tests in 114.55s, 39 Hub/HTTP
tests in 31.81s, and 99 adapter/negotiation/profile tests in 72.44s. Meet/Persona
UI tests passed (205), both Angular template checks passed, and the standalone
Worker boundary detector passed for 87 files. These batches overlap existing
regressions and are not a count of newly discovered bugs.

Against private Meet build `f6b9255`, actual Chromium and Firefox receivers
decoded the normalized red/blue silent clip, replaced image/video generations,
and retained independent speech/screen. Controller loss removed the avatar in
1826.62/1716.84ms respectively. The initial invocation lacked FFmpeg and failed
before browser setup; explicitly selecting the existing extracted FFmpeg tools
resolved this environment issue without changing application policy.

The actual current-source Hub/Worker/browser test passed in 63.57s. Four avatar
source generations covered video, image, video and resume; pause reached the
receiver in 773.91ms and asset-policy revocation in 314.08ms (local 266.97ms).
Two independent spoken answers each completed 220500 local samples, with remote
audio correlation, continued screen frames, zero human capture and zero transform
errors. The browser-only container used the earlier pinned Worker image; this
test does not prove installation of the new Python code in an image.

All these observations use explicit synthetic/test-only policy and media. They
are not Hub-reserved production release evidence, GPU/soak/TURN verification or
public deployment. Full isolated companion check and the remaining MAP-20
pre-dispatch/multiple-session criteria are tracked separately.

The final expanded backend batch ran 375 tests in 251.36s: 368 passed and seven
failed because the router's synthetic `SimpleNamespace` fixture lacked the new
default-false field. Updating that fixture to match `DialogAuthority` required
no production-policy relaxation; all 24 router tests then passed in 21.26s.
The expanded Meet plus Persona UI selection passed 277 tests in 2.26s; ESLint
and Worker boundary checks remained green.

Companion full check at `f6b9255` passed frontend/build/static/Go gates, then
reported 779 Node passes, two failures and two explicit skips in 259.15s.
Both failures reproduce outside the avatar feature in the native raw HLS
encoder's audio queue; infrastructure gates after Node did not execute.
TBP-016 tracks the concrete fix and renewed full check. Concurrent companion
commits through `23f9517` were integrated without discarding their changes
(`609c531`), so the next complete companion check must cover the merged source.
