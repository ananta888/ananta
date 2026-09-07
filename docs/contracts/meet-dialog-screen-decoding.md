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
generation checks. The Python adapter adds a bounded completion deadline and
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
