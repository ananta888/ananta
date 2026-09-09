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
