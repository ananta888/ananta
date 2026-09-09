# Nonblocking delegated screen-frame delivery

## Source check (MAP-14/24/29)

`DialogScreenPump.tick` awaits `screen.push` in a browser RPC. Meet's decoder
already bounds a JPEG decode to one second, but that wait blocks the same
assigned loop that services the 200 ms speech queue and Hub control reads.
This is a reproducible structural scheduling limit, not an attribution of every
intermittent remote screen or audio failure seen in earlier runs.

## Incremental implementation

Move browser frame delivery into a narrow one-flight port. Start at most one
owned generation/sequence operation and return immediately; poll its closed
status on later runtime ticks. Keep only the existing latest source frame while
decoding; never queue another push, retry uncertain delivery, or grow the PCM
queue. Continue to enforce the five-FPS limit after a confirmed completion.

Preserve Meet's own decode, source expiry, membership, E2EE and post-decode
generation checks. The Python adapter adds a 1.5-second completion deadline
(one-second native decode plus bounded observation margin) and
exact page/generation/sequence fencing, and closes only its owned generation on
failure or cancellation. A late Promise may not mutate a newer operation or
close a replacement source. Closed activation races may reopen only on a later
fresh Hub update, as before; unknown errors require a new control revision.

Separate transport phase logic from source ownership and frame scheduling
(SRP/ISP/DIP). Do not introduce worker-to-worker calls or independent execution
threads. Preserve the closed offline task-owned source; this does not add
arbitrary navigation or permission to show authenticated browser content.

## Headless tests

Use pending/done/stale/failed phases and virtual clocks to test one-flight
backpressure, cadence, expiry, cancellation and late result fencing. Execute
the exact browser scripts with deterministic promises, including replacement
generations and redacted errors. Then inject a bounded real JPEG decode delay
in the private Hub/Worker/Meet browser scenario while speech plays: require
complete PCM, moving remote screen, non-silent correlated remote audio and
bounded revocation. Record failed attempts rather than hiding them with retries.
All media/policy substitutes remain explicitly synthetic; no GPU, public TURN
or production release claim follows from this gate.

## Implemented and verified

`BrowserScreenFrames` owns one token/generation/sequence and one browser phase;
`DialogScreenPump` owns the latest-frame cadence and activation lifecycle.
Neither phase polling nor cancellation retains JPEG content in phase metadata.
Cancellation and source cleanup compare the exact generation, so an old decode
cannot close a replacement. A pending decode cannot trigger source reopening
inside a Hub update. The host start floor is anchored before submission;
the browser delivery slot stays pending until 200 ms after actual successful
submission. This preserves the five-FPS ceiling without adding a second idle
interval after a delayed RPC reply; it does not promise five FPS under decoder
latency. See the [delayed-ack cadence correction](meet-screen-ack-cadence.md).
The source factory and frame port are independently injectable (SRP/DIP/ISP).
The existing pump still combines activation and frame scheduling; this preserved
small coordination responsibility was not expanded into decoding or Hub policy.

Forty initial transport/source tests passed in 24.01 s, then 57 tests including
the scheduler and exact delay JavaScript passed in 30.71 s. The final combined
speech/screen/session regression passed all 238 tests in 88.41 s with two Pytest
workers. Targeted Ruff lint/format and diff checks passed.

The real private-browser JPEG-delay gate passed in 45.13 s against Meet source
`c20f4533308ee783807af7c9396f5b51fea965a1` and its existing local build. Native
JPEG bitmap completion was delayed by 300 ms without changing the one-second
decoder watchdog or any authority limit. The observer measured 47 completed
frame deliveries at 312.70–401.83 ms. The first 220500-sample speech completed
locally and the receiver observed correlated non-silent audio; this is not exact
remote sample accounting. The selected second voice was interrupted by current
policy revocation in 589.09/605.91 ms locally/remotely while screen continued.
No human capture or transform errors occurred. Policy/audio were synthetic;
real selected-voice GPU, public TURN and long-session acceptance remain separate.
