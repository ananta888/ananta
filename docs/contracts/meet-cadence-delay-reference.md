# Bounded paired-idle cadence reference (MAP-29/30)

The second private two-hour attempt failed after 5,285.340 seconds (88 min
05 s), with unchanged inputs and one failed test, no skipped/error cases.
Its Hub TEST identity is `RUN_9ab174c5f462affa4b9e9755f473d03c` under
`SRC_b399a44bb8347179c9155f266a210d53`, Ananta `63f602b8f` / Meet `ebd78be`.
The last successful periodic record had 112 screen observations, 87 lease
generations and peak sampled RSS 2,578,440,192 bytes / 22 processes.

Screen generation 176 failed at an observed age of 916,199 microseconds against
the unchanged 750,000-microsecond freshness bound. Hub control remained fresh;
there was no reported control transport failure. Relative to frame 70's start,
the last host ticks were:

| Relative ms | Existing pump action |
| --- | --- |
| 0 | begin frame 70 |
| 456.22 | acknowledge completion; unconditional return despite a due frame |
| 599.77 | begin frame 71 |
| 1,077.08 | acknowledge completion; unconditional return despite a due frame |
| 1,541.60 | next control poll, followed by the failed pre-tick quality read |

The earlier deadline-reset correction remains necessary, but does not remove
these extra empty ticks after late completion. Two new deterministic cases
reproduce the missed opportunity. The trace simulation uses an explicitly
synthetic 25-ms completion delay, not an invented measured cross-clock latency.
The proposed correction permits one due latest frame immediately after a
confirmed completion; pending/stale/error paths do not send. One in-flight
slot, a 200-ms start-to-start minimum, no catch-up loop, generation checks,
Hub ownership and pre-tick quality checks remain unchanged.

## Actual-browser fault profile

`private-dialog-cadence-delay` is a separate closed five-minute Hub-reserved
profile. It uses the same real private browser/Hub/Worker fixture and immutable
browser/proxy images. The explicit `paired-idle-450-v1` test fault changes only
the exact owned `/machine` page's first 30 calls to the normal 100-ms idle wait:
`450, 450, 100` ms, repeated ten times. Afterward waits are unchanged. It does
not modify browser clocks, freshness limits, authority or busy-audio's 20-ms
cadence, and cannot be selected for the long or GPU reference. Every other
fixed runner profile clears the ambient fault setting to `off`.

The test requires all 20 delayed waits to complete. Reports contain only fixed
profile/counters and the explicit non-production classification. Failed waits
retain their original exception and do not count as completed. Default hooks
are inert. Recording happens after owned resource cleanup, so an observation
error cannot prevent teardown. This is an adversarial synthetic schedule, not
a claim that all host jitter follows this pattern or that five minutes replace
the separate two-hour reference.

The optional delay lives in a focused test adapter. The existing long
cross-repository scenario remains SRP/complexity debt; no new condition branches
or suppression of its complexity guard were added to hide this extension.
Validation first compares the real fault profile before/after the pump change,
then checks the installed Worker and normal longer schedule separately.

The first 75 profile/input/fault-unit checks passed in 35.48 seconds; the
subsequent nine focused hook checks, including the inert default path, passed
in 12.39 seconds. Ruff and Todo consistency checks passed. Actual fault-profile
results are still separate from these deterministic tests.
