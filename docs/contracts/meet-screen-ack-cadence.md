# Screen cadence and delayed browser acknowledgements (MAP-30)

The instrumented reference `RUN_d69d1defb6dcb1ab5896529b37e2d274`, admitted
under `SRC_ceb7e6cd42f89883910f536b7ef0c38f`, failed after 3594.392 controller
seconds / 3590.04 pytest seconds: one failure, no errors/skips, unchanged
Ananta `4134b7da6`, private Meet `848a3d6` and frontend inputs. Its last printed
progress was 3521 active seconds, 59 lease generations, 76 successful screen
checks, sampled peak RSS 2,476,990,464 bytes and 22 processes.

This failure is distinct from the earlier receiver-only symptom. The Worker
reported `meet_media_timing_source_failed`; the pending chat answer subsequently
timed out. The validated browser timing receipt reports screen generation 120,
state failed, observation 3568584300 us and current time 3569482100 us: a
897800-us age exceeds the unchanged 750000-us freshness limit. No
`dialog_screen_failure` receipt was produced, so this is not a new measurement
of receiver transform failure or proof that the prior freeze is fixed.

The final scheduling samples include a 393.45-ms screen tick containing a
363.8-ms screen browser call, followed by a 526.84-ms tick gap. Those samples
do not prove why the browser or host was delayed. Source inspection does show
another avoidable delay: the audited `DialogScreenPump` starts its next 200-ms
interval **after** `frames.begin()` returns. A delayed acknowledgement can
therefore add idle time after a frame has already reached the browser.

## Bounded correction to verify

First reproduce the scheduling defect with an injected clock and delayed
`begin` return. Anchor the existing host start-to-start floor before submission,
not after acknowledgement. Preserve the actual browser-side five-FPS ceiling
by retaining the single delivery slot until 200 ms after successful browser
submission. A poll can report pending during that bounded interval; no new
timer, queue, stream, retry loop or independently generated frame is needed.

The frame adapter owns browser-clock pacing; the pump owns source selection
and cadence. Keep the exact token/generation/navigation checks, cancellation,
1.5-second delivery deadline, fresh-Hub-only reopening and 750-ms quality fence.
Test early and late acknowledgements, unresolved decode, cancellation during
the paced slot, replacement generations, expiry and no catch-up bursts before
a real short packaged-browser reference. A same-runtime paired delayed-ack
reference should precede another two-hour run. No passing regression or causal
runtime repair is claimed by this audit alone.

SRP/DIP: extend the existing frame-delivery port rather than moving browser
clock rules into Hub policy or adding orchestration. The broad Worker runtime
and PeerMesh composition debt remains; this work does not broaden those roles.

## Implemented regression and paired reference

Three new deterministic regressions failed against the previous implementation
in 9.02 seconds: the start deadline was 100.5638 instead of 100.2, the delayed
ack trace exceeded the unchanged 750-ms freshness fence, and browser completion
released the slot before its 200-ms spacing elapsed. The two existing narrow
runtime modules now implement the correction above. All 33 pump/frame-delivery
checks passed in 27.59 seconds, including unresolved decode, cancellation while
pacing, late settlement, replacement generations and backward browser clocks.
The Worker boundary guard passed for 123 Python files. These are technical
regression results, not a completed real-browser or two-hour acceptance.

`private-dialog-ack-delay` is a closed five-minute reference using the same
immutable browser/proxy inputs and 660-second outer bound as the existing
short soak. Its explicit `paired-ack-350-v1` test hook delays one successful
owned frame acknowledgement by 350 ms, leaves the next 100-ms idle unchanged,
then delays the following idle to 350 ms. No later calls are altered.
The hook is armed only after successful startup/initial dialog checks, so the
earlier intentional screen pause cannot hide its effect on the active stream.
Success requires all three steps to have actually completed; failures preserve the
original exception and cannot be counted as completed faults. Off, long and
GPU profiles do not install this hook. The injection is test-only, not a
production cadence policy or a claim to reproduce every host scheduling pause.

The acknowledgement hook, unchanged idle hook and all profile runner checks
passed 105 tests in 71.20 seconds before the additional explicit profile-shape
assertion. These are headless unit/process checks, not the real paired run.
The final frame/explicit-profile set passed 30 checks in 25.76 seconds; after
limiting activation to the observation phase, both fault-hook suites passed
20 checks in 19.36 seconds. These counts overlap the earlier suites.

## Same-runtime delayed-ack reproduction

The BEFORE reference keeps the corrected test harness but restores only
`dialog_screen_pump.py` and `screen_frame_delivery.py` to `ae6e2cd72`.
Its private revision is `fa2607d628362ff8f3b5b4fc03d05d1b3c87834a`;
the comparison revision is `1299b92044a894e1ef87e4b5b6cb1bdebf276ef6`.
Both use private Meet `848a3d6075f4c876e0eea217e4367276ba048928`, identical
frontend digest `803a77c0f5b8076bff73c6650ffc9a62c4e48db148ec5ecca43cedb2a49b85b8`,
browser image `5d4be51c5dda` (Chromium 145) and proxy image `8fc7d306e5e4`.
The selected Ananta source digest changes deliberately; browser/runtime,
fault profile, media freshness and resource limits do not.

Hub-reserved BEFORE `RUN_f5bb578f9dec90b000694ebf202c5b46`, under
`SRC_6670659350f74053d75240c9b766202d`, failed in 28.81 pytest / 33.279 controller
seconds. All intended fault steps completed exactly once. The original
`meet_media_timing_source_failed` receipt shows screen generation two,
observation 13503299 us, current time 14320699 us and age 817400 us, exceeding
750000 us. Inputs remained unchanged; one failure, no errors/skips. This is
a reproduced scheduling defect, not an unrelated startup or incomplete-hook
failure. Its result digest is
`eb58b52b13af884793ae7d8ce399bb2e2df35d186ca4d8dae4979d3f3f7a77d5`.

AFTER `RUN_9de737dcdf1e53b97335f9b15311aba5`, admitted under
`SRC_b5586c323128ac158005e64b8591d4af`, passed the complete five-minute profile
in 309.22 pytest / 313.669 controller seconds: one pass, no failures/errors/skips,
unchanged inputs and all fault steps completed exactly once. The 300-second
Task yielded 295 active observation seconds, four renewed lease generations
and six screen checks, with sampled peak RSS 2,257,555,456 bytes / 22 processes.
Its result digest is
`2cdde80f806a2030bb7979627fdbc5f74db3548c37e53fae9020df8e4d399794`.
This paired result verifies the delayed-ack correction with unchanged limits;
it is not a completed two-hour stability acceptance.

These references execute snapshot-pinned host-side Worker code against the
packaged browser. They do not prove that the unchanged browser image contains
the new Worker code, that the receiver-only freeze is repaired, or that GPU,
public TURN and the separate two-packaged-publisher gates pass. The identities
are synthetic TEST evidence and cannot satisfy production release policy.

## Packaged correction

The complete existing Dockerfile built successfully from clean `1299b9204`,
reusing the unchanged locked dependency/browser cache and copying the current
source packages. The new local immutable image is
`sha256:46b062604e5579f661adacfe728b776c7ca6dd710b6f9de5fbad7e3b44adccbb`.
An isolated non-root, read-only, network/GPU-free inspection found both changed
runtime files byte-identical to that snapshot. All 35 runtime-lock/health checks
passed in 40.69 seconds, including actual packaged healthy/unhealthy/healthy
transitions, no source overlays and no Hub package. This is a cached rebuild
and current packaged liveness, not another clean dependency download or GPU
inference result. No serving image, model job, trust or public room was changed.

The separate Hub-reserved `packaged-resources` reference also passed against
this new image: `RUN_0de0f67e6d455f86b2dd57ecf176003e`, under the same AFTER
source identity, completed in 49.68 pytest / 53.830 controller seconds, one pass
and no failures/errors/skips, with unchanged source/frontend inputs. It exercised
two independently assigned packaged Workers, actual owned media delivery,
room reconnection and independent terminal cleanup. Active cgroup memory samples
were 272,265,216 / 264,024,064 bytes under each 1-GiB limit; PID samples were
103 / 104 and both returned to five, with zero active dialog slots. These are
startup/active/terminal samples, not continuous peak or GPU measurements.
The result digest is
`225cf26f4077ad8b7baa71feaff9ae6be59940225bc6f410edaa7ea2ad25c267`.

The uninstrumented `private-dialog-soak` remains the separate two-hour
stability gate. Neither the successful injected comparison nor this packaged
reconnect reference replaces that remaining acceptance.

## Uninstrumented two-hour attempt: still failed

The subsequent `private-dialog-soak`, pre-reserved as
`RUN_eb294456679c3aae2bb4d6ccc7ce9d46` under the same
`SRC_b5586c323128ac158005e64b8591d4af`, failed in 6110.17 pytest /
6114.352 controller seconds. Ananta `1299b9204`, private Meet `848a3d6`,
frontend `803a77c0`, browser `5d4be51c` and proxy `8fc7d306` remained unchanged;
one failure, no errors/skips, no cadence fault or SFrame probe enabled.
The final printed checkpoint was 6096 active seconds, 102 renewed lease
generations, 131 screen checks and sampled peak RSS 2,622,271,488 bytes /
22 processes. The result digest is
`c9b0636cb2322b7e12b6aca35e730887dca72e008e174d7694757dffd90fae3f`.

The runtime again stopped with `meet_media_timing_source_failed`. The retained
receipt has screen generation 204, current time 6097440000 us and last
submission 6096575099 us: **864901 us**, beyond the unchanged 750000-us fence.
The scheduling tail contains screen ticks of 382.16/383.83 ms and gaps up to
572.52 ms; unrelated chat/browser calls also exceed 300 ms. The Hub exchange
still accepted fresh controls and recorded no control failure. This does not
prove the origin of the slow calls or repair of the older receiver-only freeze.

The delayed-ack fix remains valid for its paired reproduction, but is not
sufficient for the complete serial runtime. Source audit now includes the
synchronous CDP acknowledgements invoked from source callbacks, per-loop
control/chat/timing RPCs and the test's additional once-per-second diagnostic
RPCs. Before another two-hour attempt, use bounded passive phase/ack/idle
measurements and a short same-runtime reference to identify where screen
delivery is delayed. Do not weaken freshness, skip controls, reuse stale
frames, claim a resource cause without measurement or reset the timing gate.
MAP-30 remains open; all receipts here remain synthetic TEST evidence.

The test-only `DialogBrowserCostObserver` now records bounded histograms and
the last twenty slow calls without changing arguments, return values or
exceptions. It separates CDP frame ACKs, source-content/activity checks,
test diagnostics, media operations and idle calls. Costs are explicitly
inclusive: an ACK invoked inside an evaluate is not an additional amount to
sum onto that evaluate. Success and failure receipts retain the projection.
All sixteen cost/soak/RPC observer checks pass in 17.44 seconds, including
nested ACK attribution, exact call counts, exception identity and detached
bounded reports. No runtime repair is claimed by this instrumentation.

## Remove redundant browser roundtrips without moving policy

The pinned Meet `MachineChatEndpoint.poll()` checks its queue and current
authority itself. `DialogChatBrowser` already maps only known closed-queue
errors to a closed result. The pump's preceding `chat.status().open` RPC
therefore does not authorize the later poll and is now removed. Idle polling
uses one browser call instead of two; a closed poll still invalidates speech
and cannot reopen without fresh Hub state. Two new call-count regressions
failed before the change (8.54 seconds); all ninety chat/revocation/speech/
runtime-composition checks pass afterward (66.54 seconds). The Worker boundary
guard passes. Existing queue bounds, ACK-before-dispatch and model policy are
unchanged. This removes avoidable serialized work, not proof that all slow
browser calls or the long-run failure have been repaired.

SRP/DIP: keep chat authority enforcement at its existing browser endpoint and
closed transport port, not a second Worker-side status decision. The broad
runtime composition remains preserved SRP debt and is not expanded here.
