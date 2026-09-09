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

## Deterministic correction

The captured missed-tick case and three fast/slow-completion cases failed
against the old pump (four failed, six existing cases passed; 13.24 seconds).
Removing only the completion-time deadline reset fixes that scheduling defect.
The next normal tick may submit one latest frame when the original interval
has elapsed; a pending decode still owns the sole slot and cannot trigger a
catch-up burst or reopen itself after stale authority.

All **88** pump, frame transport, owned-screen/workspace and independent timing
checks passed in **40.09 seconds**. The existing rate test now verifies actual
start-to-start spacing after a slow completion, including a subsequent fast
completion, instead of treating an extra idle interval as the rate limit.
Source freshness, quality polling order, frame decode timeout, Hub authority
and browser cleanup are unchanged. Ruff and whitespace checks pass.
Fresh private/native and installed-image verification remain pending; this
unit result alone does not close the failed long-run acceptance.

The closed test runner now also reserves an explicit five-minute intermediate
profile (`private-dialog-soak-smoke`, 300-second fixture / 660-second process
bound). It cannot masquerade as the two-hour profile or accept arbitrary test
commands. All 56 runner/input checks passed in 29.90 seconds, including exact
profile/image selection, environment isolation, mutation rejection and owned
process timeout handling. The intermediate actual run is still separate.

## Actual corrected-source and installed-package checks

At Ananta `51377daa0` / Meet `2d39a17`, the following pre-reserved TEST runs
passed with unchanged inputs. Their source identity is
`SRC_e41a0251956a34c5b05146dbb00f41fc`; none is production eligible.

| Profile | Actual result |
| --- | --- |
| Private isolated-peer short | `RUN_bf75d4df0a914e6c8db961ddd07a1477`, 37.186 s runner duration; real correlated chat, moving screen, pause/resume, private-marker exclusion and stop. |
| Five-minute intermediate | `RUN_19353574cf4ec8076169266b50820c7e`, 314.103 s runner / 309.84 s pytest; 295 s active observation of the 300-s task, four lease generations, six screen samples, peak sampled RSS 2,231,808,000 bytes / 22 processes. |
| Two installed Workers/resources/reconnect/media | `RUN_6ab4779bb83cd2215ca5272a12d96b09`, 56.635 s runner / 52.46 s pytest; actual independent packaged publishers and existing media/recovery/stop assertions passed. Active memory samples 271,024,128 / 265,662,464 bytes with 107 PIDs each; terminal zero active slots and five PIDs each, within unchanged limits. |

The immutable image is
`sha256:ea1d671b66705782014ad3df2b5eead35d634df21aebcc2a8f97d13e9f2c2bcf`,
built from runtime source `a10b0e9c9efe8acb24b3e48383b2f7601f60c59b`.
A separate owned no-network/read-only container read the installed pump file;
its SHA-256 exactly matches source
`a3e8fa19447fbcc683451f122c402221741e5c3194cd5a868e589901045e7ee4`.
The short/intermediate fixtures use a host-side executor and this image as
isolated browser host; the third run actually executes the packaged Worker
code without source mounts. These are different topologies, not an invented
GPU/public reference. Existing serving containers were not replaced.

The next grouped Meet check and two-hour repeated profile remain distinct.
The failed 45-minute run is not erased by these shorter corrected-source passes.

## Grouped compatibility and repeated long reference

The isolated Meet `ebd78bed6013471ad0eec6d52adfbbbb56293b6c` grouped check passed:
1,142 frontend tests, 1,066 Node/browser passes, zero failures, four explicit
Node skips; Node duration 380.153 seconds. Build (7.994 s), Go/unit/vet and
static gates passed. Fourteen external infrastructure opt-ins were visibly
skipped. Both independent speech tests completed 66,150 played samples with
zero capture/transform errors. The full result and exact scope are recorded in
the companion's `docs/ananta-linux-grouped-check-20260909.md`.

The repeated private two-hour run then reserved
`RUN_9ab174c5f462affa4b9e9755f473d03c` under
`SRC_b399a44bb8347179c9155f266a210d53`, with Ananta
`63f602b8f51dee23804f707e773be4f70fb32a3f`, the same fixed Meet snapshot and
the immutable `ea1d671b6670…` Worker described above. Its input worktrees and
bundle stay unchanged during execution. This reservation is TEST/synthetic
only and, at this checkpoint, still running rather than accepted evidence.
The improved stdout setting now exposes numeric periodic progress while the
test is actually running. No other large owned test/GPU run is launched in
parallel with the long media reference.
