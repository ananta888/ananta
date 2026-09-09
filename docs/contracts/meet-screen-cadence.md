# Screen cadence after asynchronous frame completion (MAP-29/30)

The pre-reserved private two-hour profile at Ananta `3d7cd3073` / Meet
`962f678` failed after **2732.759 seconds** on 2026-09-09. It observed 45 lease
generations and 58 moving-screen samples before the Worker terminated with
`meet_media_timing_source_failed`. Peak sampled RSS was 2,422,620,160 bytes
across 22 processes, below the unchanged 3 GiB/80-process test ceiling.
Inputs remained unchanged. This is a failed TEST/synthetic run, not a two-hour
pass or production release:
`RUN_3a4d8ef5d509c68d00fa592df88580d1` /
`SRC_e19b1622c51a9ed7ce0bac59ebf8eb0c`.

The screen's failed browser projection had generation 92 and observation age
854,400 microseconds, exceeding the existing 750,000-microsecond boundary.
The last recorded Hub control was accepted with over two seconds of freshness
remaining and no control/transport failure. The source-quality fence must
remain unchanged.

## Source-confirmed scheduling defect

`DialogScreenPump.tick` sets its next frame time to 200 ms after beginning a
frame, then adds another 200 ms after observing asynchronous completion. In
the captured final sequence, frame 19 began at relative time 0, completion was
observed at +132.13 ms, and the next tick at +274.17 ms sent nothing because
completion had postponed the deadline to +332.13 ms. The following tick came
at +744.45 ms. This unnecessarily discards a timely opportunity to submit a
fresh frame. The trace proves that scheduling sequence; it does not prove the
cause of every browser/host delay or the unrelated wall-clock discrepancy.

Retain the existing start-anchored 200-ms interval instead of postponing it at
completion. Keep one in-flight frame, no catch-up loop, the same maximum five
starts per second, current-generation cleanup and fresh-Hub-only reopening.
Do not move or relax the source timing/authority fence, replay an older frame,
or create an independent Worker orchestration loop.

Reproduce the missed tick deterministically from the recorded relative times;
test fast/slow decode, rate ceiling, pending/stale/cancellation and the shared
browser-workspace pump. Then run fresh private short/intermediate/long gates
and verify the new installed Worker separately. The old failed run stays
recorded regardless of subsequent success. Scheduling, frame transport and
Hub authority remain separate responsibilities (SRP/ISP/DIP).
