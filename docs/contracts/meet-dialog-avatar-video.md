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
