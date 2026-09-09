# Receiver freeze in the normal dialog reference

The post-restart reference at Ananta `5a7be27f5`, Meet `c4ef486` and immutable
browser image `5d4be51c5dda` failed after 2161.131 controller seconds / 2156.82
pytest seconds. The Hub-reserved TEST run
`RUN_9c1e519283f6ad02421b85b96d0a4e86` used
`SRC_6b5a3906023621addc28d9aa17359fe6`; all selected inputs remained unchanged.
One test failed, with no errors or skips. The terminal report and JUnit remain
in the persistent private reference directory, not a deleted `/tmp` workspace.

At failure the Worker remained active, with 36 observed lease generations and
no reported runtime exception. The last successful progress report was at
2088 active seconds, 35 lease generations, 45 screen checks, peak sampled RSS
2,389,725,184 bytes and 22 processes. This is not a completed two-hour soak.

The newly retained `dialog_screen_failure` property reports:

- Receiver ICE/connection connected, signaling stable, zero ICE failures.
- 589 inbound video packets, zero decoded frames/keyframes and zero PLI/NACK.
- One video element with width zero and ready state zero.
- No reported structural SFrame transform failures or Worker-load failures.
- Last source observation: live source/lease, active E2EE, open screen
  generation 145 and frame sequence 47; source and receiver samples are not
  atomic. Later teardown scheduling is not the exact assertion-time state.

This reproduces the earlier roughly 36-minute receiver symptom and separates
it from the already repaired producer cadence failures. It does **not** yet
prove missing keys, a timestamp overflow, lost keyframes or a track-binding bug.
Ordinary first-keyframe-loss tests pass in both Chromium and Firefox and request
a replacement keyframe; their PLI behavior differs from this failure. A bounded
40-second host clock observation found no material wall/monotonic divergence
(less than 0.007 ms), not proof that the entire run had no clock correction.

Next isolate repeated source replacement beyond the existing 18-activation
browser regression, with real source/lease authorization and unchanged SFrame,
frame, receiver and cleanup bounds. Separate a fast churn reproduction from
elapsed-time failures; neither is a substitute for the full soak. Add only
closed, content-free diagnostics needed to distinguish transform delivery and
decoder state. Keep runtime fixes contingent on a reproducer, then verify the
regression and actual normal long reference. Do not weaken quality or authority
limits to obtain a pass.

The existing broad native integration fixture and PeerMesh composition remain
SRP debt. Reuse their narrow fixture/media ports for the reproducer rather than
introducing a second orchestration owner or mixing Hub policy with decoding.
