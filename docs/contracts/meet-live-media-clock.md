# Bounded live media clock: MAP-24 implementation plan

Source audit: Ananta `cfe01b6cc`, Meet `12235df`, 2026-09-09. Speech already
has exact PCM sample progress; avatar video uses an independently owned muted
decoder; avatar/screen canvas publications have separate generation and stop
lifetimes. The browser currently exposes no common live timing projection.
Offline MP4 end-time validation is not a live receiver synchronization proof.

Implement the following additive slice before the hardware/soak acceptance:

- One machine-page-owned monotonic timeline, bounded by that page's membership
  generation. Source lifecycle tokens are separate from identity, Hub grants
  and evidence. Never compare raw timestamps from different processes.
- A small source-neutral timing guard maps independently started media clocks
  to that timeline. Observe speech sample progress, decoded avatar-video media
  time, and screen/canvas frame submission separately. Record both observation
  time and media position, never disguise a submission timestamp as delivered
  RTP or decoded receiver evidence.
- An additive closed timing probe/projection identifies actual support and
  measurement kind. Existing source receipts and legacy capabilities retain
  their exact shapes. An explicitly negotiated Hub execution option requires
  the new port; an old browser cannot silently satisfy it.
- The initial fixed profile is independent persona video plus speech, not
  generated lip synchronization. Predeclare maximum source-clock drift of
  500 ms and observation age of 750 ms after source readiness. Existing source
  setup, 2.5-second Hub freshness, original Task/lease and recovery bounds are
  never extended by a timing observation. Invalid/regressed clocks, stale
  generations and exceeded thresholds fail closed and discard pending source
  output; a source restart cannot reuse its previous timeline lease.
- Preserve the existing Hub GPU FIFO admission and speaker floor. Audit and
  compose session and aggregate publication budgets at Hub admission, not in
  a Worker loop; fixed dimensions/rates/queues and exact publisher role remain
  upper bounds. No host-wide arbitration of unrelated GPU jobs is claimed.

The canvas API requests capture without accepting an application timestamp;
therefore this implementation must not claim it rewrites browser RTP clocks.
Web Audio also has its own rendering clock. These boundaries follow the
[W3C canvas capture specification](https://www.w3.org/TR/mediacapture-fromelement/)
and [Web Audio clock definitions](https://www.w3.org/TR/webaudio-1.1/).

Verify the pure guard with virtual clocks and hostile closed projections;
verify late callbacks, independent close, source replacement, loop/hold video,
audio underflow and clock regression in actual source adapters. Then exercise
real Chromium/Firefox receiver timing, synthetic synchronized markers, and
separately the installed GPU/TTS/video path under a declared hardware profile.
Report source drift, receiver measurements and semantic lip-sync capability
as different facts. Failed quality gates stay failed, never merely logged.

SRP/DIP: clock math, source observation, browser transport, Hub profile admission
and receiver-quality measurement are separate small ports. Preserve the
existing broad machine-page/Worker composition for now; do not add another
scheduler, global identity issuer or policy authority there. MAP-24 remains
partial until these implementations and actual acceptance are complete.

## Closed observation contract and execution-side fence

The first implementation adds `ananta.meet-media-timing.v1`, fixed profile and
`browser-performance-v1` timebase. Its epoch and up to three source generations
are local lifecycle counters, never evidence identities. Each source records
start, observation and media-position timestamps separately. A held decoded
video retains its last media timestamp while requiring a fresh rendering
observation; a canvas submission has no invented media position or drift.
Drift arithmetic is independently checked against the reported origin and
position. A failed source remains explicitly failed.

`MediaTimingGate` pins the expected epoch and permitted source kinds, rejects
rebasing within a generation, retains retired-generation fences and checks
browser time advance against the independently sampled Worker monotonic clock.
Invalid data or failed quality irreversibly closes the gate. Returned snapshots
cannot mutate its private comparison state. It has no grant, source-open,
retry, Task or Hub-service port.

Thirty-nine contract/fence tests passed in 23.60 seconds; the standalone Worker
boundary guard passed for 120 files. These are synthetic virtual-clock tests.
The browser producer, explicit Hub negotiation, runtime composition and actual
receiver/hardware checks are still required: these classes alone do not claim
an active live-clock feature or completed MAP-24 acceptance.

## Browser port and native-source observations

Meet now implements an opt-in `timing.probe/start/snapshot` port. Its separate
closed probe reports the fixed profile/timebase/limits and actual native
`decoded_video` / `canvas_submission` availability without opening a source.
Ananta's strict probe consumer and `BrowserMediaTiming` transport preserve
authority checkpoints around each RPC, reject already-active initial sources,
bound polling to 10 Hz and irreversibly close on failed observations. The
browser's own 100-ms watchdog remains independent of Worker polling.

62 contract, fence and transport tests passed in 31.33 s. Meet's private
Chromium-publisher / Chromium-and-Firefox-receiver matrix passed, plus a negative
Firefox-publisher feasibility case (three cases, 20.769 s). Firefox lacks the
existing required canvas `requestFrame` API here; the installed Worker remains
Chromium and there is no permissive capture fallback. Peak observed source
drift was 147,600 / 144,000 us video and 8,200 us PCM; intentionally unrefreshed
screen sources stopped after 799.87 / 790.14 ms. Actual loop/held-frame pixels,
speech and screens were received without human capture or transform errors.
These do not measure end-to-end A/V alignment and are not GPU/public/release
evidence. Hub option admission and actual installed Worker composition remain
next; the new Python transport is not yet called by `dialog_runtime`.

## Hub negotiation and runtime composition

`ANANTA_MEET_MEDIA_TIMING=1` explicitly selects the fixed profile for new
Hub-owned dialog assignments; the default is zero. A caller cannot inject this
field through the start payload. Only exact `media_timing: true` is additive
in the stored context and signed Worker assignment. Preauthorization, phase
and recovery bindings include it without changing legacy recovery digests.
Changing Hub mode while an older differently negotiated task runs fails closed;
upgrade both repositories and every Worker and drain earlier assignments
before enabling. This is not a browser-decided fallback or a live migration.

The Worker starts timing only after matching fresh Hub and browser membership,
before source updates, and checks it during the existing bounded control loop.
Failure uses the existing source/session cleanup. Renewal retains the timing
fence; a Hub-authorized new membership starts a new local epoch. The separate
browser watchdog remains active when Worker polling stalls. No timing result
refreshes Hub authority, grants consent or extends the original Task deadline.

192 focused Hub/Worker negotiation, original binding, bootstrap, runtime,
preauthorization and legacy transport tests passed in 109.18 s. The existing
large authority reader remains SRP debt: the new option validation is a separate
policy helper rather than adding another conditional to its complexity limit.
No source lifecycle or scheduling moves into that helper. Full packaged Worker
acceptance using the actual option remains required before calling this an
installed integration result.

The final Hub publisher router now also compares `media_timing`, `speaker_floor`
and `reconnect` against the current authority. Six negative cases first exposed
that these options could be added or removed at dispatch without rejection;
the exact-match fence rejects all six before contacting either Worker. All 49
router, timing negotiation and assignment transport cases then passed in
28.97 s. This is a dispatch-binding correction, not an explanation for unrelated
intermittent browser timing failures.

The first packaged two-Worker timing/reconnect gate failed during Hub admission
in 31.61 s: the phase coordinator omitted `media_timing` when reconstructing
the authoritative phase digest. The stored binding correctly included it, so
startup failed closed before browser dispatch. The coordinator now preserves
the flag. Two real SQL-Task regression cases cover startup, join, coordinator
restart and rejection of flag removal, with and without reconnect. All 72
phase/model/negotiation tests passed in 34.70 s; the live gate must be repeated.

The repeated installed integration passed in 63.68 s at Hub `e3357e38d`, immutable
Worker image `bd2445226c601c9b142c671979060d473d3bf92234168cf8780ead387274fb8b`
(Worker source `d09eac538`) and private Meet frontend `95d0d4e`. Both role-bound
containers negotiated timing, published independent moving screens/personas
and interrupted active synthetic speech. Two actual membership recoveries took
6,486.81 / 6,449.41 ms without redispatch, inherited consent or old audio replay.
The third loss exhausted the original allowance and stopped in 230.62 ms; the
other publisher remained active. No source mounts or public deployment changes.
This is a single-host synthetic-policy integration observation, not GPU/TTS,
end-to-end lip alignment, a two-hour soak or Registry-backed release evidence.

## Remaining Hub resource admission slice

Source audit after the live gate: private GPU generation already has a durable
FIFO and the Worker independently caps concurrent browser processes at two.
The Hub publisher router still has no cross-process dialog/session budget.
Meet applies audio/video sender ceilings through its existing quality policy,
but an estimated admission reservation must not be described as a packet-level
network shaper or as guaranteed sender support.

Add a separate small Hub admission port around the already-selected publisher,
using existing WorkerSlotLease records and a database-serialized resource pool.
Preserve the original Task, destination and all current authority checks; do
not create another scheduler or Worker-to-Worker routing. Bound total sessions,
per-publisher sessions and conservative aggregate publication cost (maximum
room fanout and existing source ceilings), with four FIFO waiters and a ten-
second wait. A caller cannot choose budgets. Persist the operator profile at
the pool so differently configured Hubs cannot silently share it.

After uncertain dispatch, retain capacity through the original deadline plus
cleanup grace. Observing a terminal Task is not immediate proof of process
exit: the current pre-first-progress supervisor allows 90 s, so any terminal
reclamation must retain a conservative 95-s quarantine. Keep late/replayed
reservations fenced, fail closed on pool-policy changes and re-read the exact
Hub authority while waiting and immediately before dispatch. Hardware/network
measurements must separately validate the admitted reference profile; this
reservation does not arbitrate unrelated processes or guarantee free VRAM.
