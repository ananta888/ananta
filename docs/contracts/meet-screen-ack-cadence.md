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

The owned status view also repeated the same DOM update for every accepted
control receipt. It now keeps only the three validated scalar activity values
after a successful render; unchanged state adds no DOM mutation or RPC.
Validation and revoked/closed checks occur before this comparison, mutable
caller dictionaries are not retained and a failed render is never cached as
successful. Capture, content checks, source clocks, frame freshness and
five-FPS submission remain unchanged. The new duplicate-render regression
failed before the change (one failure/two passing guards, 8.98 seconds).
All 47 source/pump/frame checks pass (37.40 seconds); after formatting the
small legacy source/test modules, the final 14 source checks pass again
(15.32 seconds), with Ruff and the Worker boundary guard clean.
The existing source owns both its fixed presentation and CDP adapter; this
small preserved SRP concern is not expanded into policy or shared caches.

The passive five-minute baseline at `ffe6ea1a9` passed before either of these
runtime optimizations: `RUN_cf26ce4c5ee4b9644e5a01fade0cfe8c`, under
`SRC_84240704ac0743957560b38e1bd4ee37`, 310.30 pytest / 314.92 controller
seconds, unchanged inputs, no failures/errors/skips. It observed 295 active
seconds, four renewed generations, six screen checks and sampled peak RSS
2,252,963,840 bytes / 22 processes. The result digest is
`66514493b23f9b72abc7b784b2385a0ae4edd2d992577a0d47fb84a77710dfa8`.
Its 1,468 CDP ACKs took at most 9.1 ms, source-content checks at most 14.38 ms
and test diagnostic calls at most 9.78 ms. Chat made 4,629 calls; unchanged
activity was rendered 273 times. Two screen calls exceeded 250 ms (maximum
362.29 ms), and one idle reached 341.62 ms. This early-run baseline does not
reproduce the sustained slow calls at the end of the failed long reference;
it specifically does not establish CDP ACKs as that failure's cause.

The corresponding optimized reference at `a0be4d513` also passed:
`RUN_4d7674e9f23dad67bc99b5b6b8f04ea3`, under
`SRC_d719b6bba7380fb7dbe5267dba945343`, 310.64 pytest / 315.271 controller
seconds. The same Meet/frontend/browser/proxy inputs and 300-second profile
remained unchanged; one pass, no errors/skips. It again observed 295 active
seconds, four renewed generations and six screen checks; sampled peak RSS
was 2,275,291,136 bytes / 22 processes. Its result digest is
`ed2b2bd0344280f6bd554818eab23dad670e91bf37c49542f057ae2f1d6e196b`.

Chat calls fell from 4,629 to 2,254 and activity renders from 273 to three.
CDP ACK count stayed at 1,468, consistent with retaining the actual source
capture instead of replaying cached pixels. Screen-call maximum was 28 ms,
but one chat call still took 322.5 ms. These samples are not a controlled
CPU-latency benchmark, and equal fixed task duration cannot show a faster
task completion. They verify reduced redundant RPCs with functioning media,
not the absence of all long-run timing failures or a repaired receiver freeze.

## Optimized long reference: freshness failure after 112 minutes

The subsequent uninstrumented `private-dialog-soak` at `a0be4d513` failed:
`RUN_f38f19dbcf0ac93bfdb98232296340a1`, reserved before execution under
`SRC_d719b6bba7380fb7dbe5267dba945343`, completed with one failure, no errors
or skips in 6764.93 pytest / 6769.585 controller seconds. The same frozen
`848a3d6`/`803a77c0`/`5d4be51c`/`8fc7d306` inputs remained unchanged. Neither
cadence fault injection nor the SFrame pipeline probe was enabled. The result
digest is `5c3ff57c4d642f3aad1758b259e4ca33ad14826fa5ca2fd12b9e31fed5fa82a6`;
JUnit digest `dc5cbb5c2ca84271fbd08de11f02f815f0ae9a4319842bb7e43016768ef1c075`.

The last progress checkpoint was 6720 active seconds, 112 lease generations,
144 screen observations, sampled peak RSS 2,662,424,576 bytes and 22 processes.
Screen generation 226 failed with `meet_media_timing_source_failed`: current
time 6751448399 us minus observed submission 6750572500 us is **875899 us**,
exceeding the unchanged 750000-us fence. This is not a completed two-hour
acceptance, and the earlier RPC reductions do not establish long-run repair.

The scheduling tail includes a 411.01-ms screen tick, 399.73-ms screen RPC,
585.4-ms tick gap and 334.55/410.5-ms chat calls. Inclusive idle calls can
contain CDP acknowledgements; their times must not be summed again. Fresh Hub
controls continued to be accepted, without a control failure. There is no
receiver-failure receipt here and no established GPU, memory or decoder cause.

An attribution bug was also found in the passive observer: its substring
classifier calls `ScreenFrameDelivery.POLL` a timing operation because the
JavaScript comment contains "timing". The reported 84,193 timing-tagged calls
therefore cannot be interpreted as 84,193 timing snapshots. Correct the closed
operation tags without adding browser calls before comparing phase costs.

The next bounded source audit targets the screen pump's pre-send status RPC.
The delivery port already checks open state, exact generation and sequence
atomically in its START operation before pushing. A separate earlier status
read cannot authorize that later push. Keep source ownership/content checks,
fresh-Hub-only reopening, the single in-flight slot and all timing fences;
first reproduce the extra call and verify closed/stale activations still deny
delivery. This is a candidate optimization, not yet a causal repair claim.
All receipts remain synthetic TEST evidence; MAP-30/31/32 stay open.

The bounded correction is now implemented: the pump no longer reads status
before taking a frame; START retains its own atomic authority/generation/
sequence check. A stale result clears the host lease and cannot reopen it.
The passive observer recognizes the exact existing delivery expressions before
its broad fallback tags, separating START/POLL/CANCEL/CLOSE and status without
adding calls or retaining arguments. Seven new assertions failed before the
changes (49 other checks passed, 41.91 seconds); these represent two findings,
not seven independent defects. All 111 focused pump/frame/source/lifecycle/
observer/timing checks pass afterward in 77.69 seconds, including execution of
the actual JavaScript against closed, wrong-generation and wrong-sequence
sources with zero pushes. Ruff and the 123-file Worker boundary guard pass.

SRP/DIP: source validation stays in the owned-source port; submission authority
stays at the browser delivery boundary. The existing synchronous runtime loop
is preserved debt, not expanded with a second scheduler or a frame backlog.
A normal same-runtime short reference is still required; these unit/regression
passes alone do not prove the 112-minute symptom has been repaired.

The normal five-minute reference at `8fc5c92a3` passed under pre-reserved
`RUN_439ebf753397801bb73e4456e66d7431` /
`SRC_3fc1f7a391da5e93aef000c71fda0734`: 309.32 pytest / 313.856 controller
seconds, one pass and zero failures/errors/skips, unchanged frozen inputs.
Meet `848a3d6`, frontend `803a77c0`, browser `5d4be51c` and proxy `8fc7d306`
are unchanged. It measured 295 active seconds, four renewed generations,
six screen checks and sampled peak RSS 2,270,609,408 bytes / 22 processes.
The result digest is
`90b6b324dffd6746e0fbb813eb1183a91bb172d4708a3b1c297c9f027aae35d1`.

Corrected attribution reports 11 screen-status calls, 1,168 delivery starts,
2,320 delivery polls and 2,366 timing snapshots. Source capture still produced
1,467 ACKs; activity rendered three times. Maximum START cost was 24.09 ms,
POLL 267.51 ms, timing 21.75 ms, chat 25.36 ms and ACK 13.15 ms. Three idle
calls exceeded 250 ms (maximum 289.11 ms). This validates the removed
per-frame status work with actual media and retained authority, not the
absence of long-run jitter. Do not compare the old misclassified timing
bucket directly with the new one or declare the failed two-hour gate closed.
