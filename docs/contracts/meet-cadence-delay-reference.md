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

## Before-fix native reproduction

At Ananta `3ece4183f` / Meet `ebd78be`, the real fault profile failed in
33.361 runner seconds / 29.04 pytest seconds under
`RUN_88786bfb62746cbafefa85cd95338f90` /
`SRC_a8e57386d5f98b35f9f743b4442875f8`. Inputs were unchanged; one test
failed, none skipped or errored. Four delayed waits completed before the
screen timing source failed at age 915,300 microseconds. The initial moving
screen assertion then failed. This is actual browser reproduction under a
synthetic schedule, not a production or two-hour pass.

An earlier attempt, `RUN_371bb4eea78a8c1d6f61b7fc0c90d7a2`, never reached
media: Docker's predefined address pools were exhausted. One confirmed empty
fixture network was removed before the retry; no serving network or container
was removed. Inspection also found that cross-repository teardown removes the
bridge-owned network before the externally owned peer browser. That resource
ordering defect needs its own correction and checks.

The late-completion correction is now implemented in the existing focused
screen pump; 108 related pump, frame-delivery, browser-screen, timing and
contract tests passed in 46.30 seconds. Worker boundary and Ruff checks passed.
The immutable updated image and actual after-fix reference followed below.

## After-fix native result

The identical fixed fault profile passed at Ananta `afc5eeb7c` / Meet `ebd78be`
in 315.847 runner seconds / 311.58 pytest seconds, with unchanged inputs and
one pass, zero failures/errors/skips:
`RUN_f168a00e2c54f42d27cfc6b14f1a0faf` /
`SRC_25e80519a82b97f9b02afba8c95d39ba`. The immutable browser image was
`sha256:5d4be51c5dda34636602c7a4782480d8f930a19f2d186c91e7e10b661a8f4b10`,
built with the full Worker revision `968a15c200b61f4a5e5f481bd8dd15bf8b7519f6`.
The text dialog executor itself ran from the host snapshot, as in the before
reference; installed-Worker execution remains a separate reference.

All 30 targeted idle calls and all 20 delayed waits completed. The 300-second
Task supplied 295 active observation seconds, four lease generations and six
periodic screen observations; peak sampled RSS was 2,253,651,968 bytes across
22 processes. Chat/re-consent, private-frame exclusion and final stop passed.
The exact captured private network ID was absent after automatic teardown.
This demonstrates the correction under this actual-browser synthetic fault;
it does not replace the normal two-hour reference or establish GPU/public
readiness. Production eligibility remains explicitly false.

## Installed Worker follow-up

After the updated Meet `c4ef486` full check passed, the separate two-installed-
Worker reference passed at Ananta `4c53b51f3` with the same immutable `5d4be51c`
image. `RUN_40a85ee7647e098bfeba13355d7bbc26` /
`SRC_2f8d8127957c93af4b193f1e71e529cf` completed in 58.910 seconds, one pass,
zero failures/errors/skips and unchanged inputs. Unlike the single-dialog
fault test, both dialog executors actually ran from their installed image,
without source bind mounts.

Both independent media publishers, room reconnection and stop passed. Active
cgroup memory samples were 270,184,448 / 266,997,760 bytes under each 1-GiB
limit; PID samples were 108 / 104. Both returned to zero active dialog slots
and five PIDs after stop. These are startup/active/terminal samples, not a
continuous peak, GPU or long-run measurement. The current broader Ananta
regression and normal two-hour reference remain separate verification steps.

## Third normal reference: receiver movement failure

The normal, non-fault-injected two-hour profile at frozen Ananta `d29d1780a` /
Meet `c4ef486` / image `5d4be51c` failed after 2,166.283 runner seconds
(2,162.03 pytest seconds), not two hours. Inputs remained unchanged, one test
failed and none skipped or errored. Hub TEST identities:
`RUN_4c3ab71f6f6734f21aed51c7032dbc19` /
`SRC_1a79f19adf72bffe8407a72d1ce302c3`. The last periodic success had 2,090
active seconds, 35 lease generations, 45 screen checks and peak sampled RSS
2,370,232,320 bytes / 22 processes. Failure occurred at generation 36.

Unlike the earlier cadence failures, the immediate assertion reported
`screen_not_moving` from the peer's twelve-second decoded-pixel check while the
runtime was not completed and had no recorded error. This does not yet establish
whether the cause is source, sender, decoder or UI selection. The bridge already
returned content-free receiver RTP/frame/state values, but pytest abbreviated
the assertion representation and those values were not separately persisted.
The scheduling property was recorded later during teardown, so it is not an
atomic receiver-failure snapshot and must not be misrepresented as one.

Next: persist a bounded allowlisted screen-failure projection immediately before
the assertion, covering source activation and receiver decoded/RTP counters.
Keep the original failure, budgets, media policy and twelve-second observation
unchanged; no retry, wider freshness bound or optimistic pass. Verify redaction
and exact failure behavior deterministically, then collect a fresh real reference.
The existing large integration fixture remains SRP debt; projection belongs in
a separate passive test adapter, not additional orchestration branches.

The passive adapter is implemented in `tests/meet_dialog_screen_observation.py`.
Both initial and periodic screen assertions first retain its closed JUnit
property on failure only. It records fixed connection states, numeric RTP,
decoded/keyframe/PLI/NACK counts, bounded fixed transform-failure categories,
video readiness and the last separately sampled source activation. It makes no
additional browser calls, changes no assertion and adds no retry. Source and
receiver observations are explicitly not atomic. Unknown values become null,
all nested rows are bounded and no arbitrary fields or values are preserved.

21 related screen/scheduling/RPC/control/transport observation tests passed in
29.44 seconds; the final twelve screen-projection tests passed in 17.91 seconds
after retaining the existing fixed transform-failure categories. Ruff and all
123 Worker boundary checks passed. No media runtime or immutable image change
was made. A fresh short check and diagnostic reference follow on the same Meet
`c4ef486` bundle; the historical failed run remains failed.
