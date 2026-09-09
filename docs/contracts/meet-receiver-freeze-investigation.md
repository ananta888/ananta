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

## Fast isolation and next diagnostic

The private c4ef486 frontend passed Chromium 80-source churn both with one
renewal (98.233 s) and 39 renewals (98.630 s), Firefox 80-source/39-renewal churn
(100.039 s), and Chromium 40 active audio/chat/screen phases with 39 renewals
(61.391 s). The latter checks at least 16,000 actual PCM samples in every
phase, correlated chat, source pixels and final revocation. These unreserved
technical observations do not prove a fix or a normal elapsed two-hour run.
The corresponding forty-phase Firefox reference also passed in 66.197 seconds.

The native fast checks use the companion's default local Playwright launcher,
not the packaged browser used by the cross-repository reference. The installed
companion package is Playwright `1.62.1`, whose browser manifest selects
Chromium `151.0.7922.34` / Firefox `153.0`; the local Chromium binary also
reports `151.0.7922.34`. Read-only inspection of immutable image
`5d4be51c5dda` instead confirms Playwright `1.58.0` and headless Chromium
`145.0.7632.6`. These inspections are technical observations, not retroactively
reserved browser-version evidence for the earlier native checks.

Matching frontend bytes therefore do **not** make the native churn/keyframe
checks a same-browser-runtime comparison. Their passes cannot exclude a
Chromium-145-specific failure. The private cross-repository driver intentionally
pins Playwright 1.58.0 to match its server; never point a 1.62 remote client at
that server or silently replace the active image. After the frozen run ends,
any version comparison needs separate exact-image/driver bindings and the same
source, policy, media and observation limits. Prefer a short same-runtime
reproducer before paying for another long acceptance. No browser upgrade or
causal repair is claimed by this clarification.

The companion now provides a test-only SFrame pipeline probe for the private
bridge receiver, with separate native-stream unit and Chromium/Firefox checks.
It counts input, enqueue, drops, thrown transforms, pipe termination and key
setup/clear commands without exporting media, IDs or keys. The Ananta JUnit
projection preserves only the closed counters, at most four Workers and sixteen
rows per Worker. Missing/unavailable measurements are not filled with success.
There are no periodic extra browser RPCs; collection occurs on screen failure.

The private served Worker response is instrumented by the fixture; its source
is included in the companion test snapshot. It is not a claim of uninjected
frontend execution, and enqueue/key-command counts are not decoder/key-install
receipts. Production code, actual source/lease/framing limits and on-disk bundle
are unchanged. A subsequent causal repair still needs an uninjected reference.

The Hub runner selects this explicitly with `private-pipeline-smoke` (ordinary
short task, 360-second outer bound) or `private-pipeline-soak` (7,200-second task,
7,560-second outer bound). Their reference identities differ from the normal
profiles. Every other profile forces the injection flag off, overriding ambient
settings. The probe returns only the four latest rows per Worker and caps its
envelope at 6,000 characters inside the unchanged 8-KiB bridge response limit.
Startup must actually observe an enqueued receiver keyframe through the probe;
an older companion that ignores the opt-in cannot silently satisfy this profile.
95 runner/projection/soak-observer checks passed in 66.45 seconds; the final
15 projection/startup checks passed in 16.02 seconds (overlapping coverage).
The final companion probe checks passed nine native-stream/collector cases in
1.153 seconds and the Chromium/Firefox receiver pair in 7.738 seconds.

## Pinned integrated diagnostic checkpoint

The instrumented cross-repository smoke passed at Ananta `4134b7da6` and
private Meet derivative `848a3d6`: one pass, zero errors/failures/skips,
32.12 pytest seconds / 36.599 controller seconds, unchanged inputs. The Hub
reserved `RUN_905b98e6c0aad2f02937566d7031f7e8` with
`SRC_ceb7e6cd42f89883910f536b7ef0c38f` before execution. The startup receipt
observed one decrypted/enqueued keyframe and four delta frames, no drops or
pipe failure; this proves the selected diagnostic was actually installed.

That private Meet revision selects only the probe, fixture/bridge opt-in,
its tests and tracking/documentation onto `c4ef486`. Production `frontend/`,
`src/` and package inputs were compared byte-for-byte and remain unchanged.
The fresh private build also reproduced the earlier frontend digest
`803a77c0f5b8076bff73c6650ffc9a62c4e48db148ec5ecca43cedb2a49b85b8`.
Only the private receiver's served Worker response is instrumented.

The same frozen inputs then started `private-pipeline-soak` under fresh Hub
reservation `RUN_d69d1defb6dcb1ab5896529b37e2d274`. Its exclusive reservation
receipt is persisted before execution in the private runtime directory.
That checkpoint was not a passed two-hour result or a repaired receiver.
The run subsequently failed after 3594.392 controller seconds with
`meet_media_timing_source_failed`: a 897800-us screen age exceeded the existing
750000-us limit, and the waiting chat answer timed out. No receiver-failure
receipt was produced in this run. Inputs remained unchanged; the previous
receiver freeze is not established as fixed. See the separate
[delayed-ack cadence audit](meet-screen-ack-cadence.md) for the measured trace
and bounded regression plan. Production-image/public-trust/GPU gates remain
separate from this TEST run.

## Matched Chromium-145 fast churn

A subsequent private native diagnostic used the frozen `848a3d6` fixture and
the same `803a77c0` frontend, with the Python installation's actual Playwright
1.58.0 Node client and local Chromium headless 145.0.7632.6. Both the local
binary and the binary read from immutable browser image `5d4be51c5dda` have
SHA-256 `27e55cc30e487493b89b663085d6deeb0707be201a568657c922fac9076c317c`.
The private launcher overrides only the test launch seam in memory; it does
not replace installed packages, public services or the frozen fixture files.

It passed 80 source activations through 39 explicit lease renewals in
99.055 seconds (100.082 seconds including Node runner overhead), no skips or
failures. Every activation checked remote decoded red/green pixels, duplicate
track delivery, exactly one receiver video/transceiver, stable signaling and
SDP below 12 KiB. Final checks found no machine capture or receiver transform
errors. No source lifetime, consent or framing bound was extended.

This removes the earlier Chromium-version mismatch for this **fast source-
count diagnostic**, not all environment differences: the binary runs natively
on the host through the fixture's ordinary launch path, not within the
packaged browser's resource/sandbox boundary or the real two-hour Hub runtime.
It is an unreserved synthetic technical observation, not retroactively issued
Hub evidence or a causal repair claim. The newer full runtime reference still
needs its own successful elapsed-time acceptance.
