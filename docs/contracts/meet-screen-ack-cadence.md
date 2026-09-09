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
another avoidable delay: `DialogScreenPump` currently starts its next 200-ms
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
