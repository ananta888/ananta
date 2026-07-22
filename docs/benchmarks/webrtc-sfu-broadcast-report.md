# WebRTC SFU Broadcast Benchmark Report

## Gate status

| Field | Value |
| --- | --- |
| Gate | `SFB-GATE-008` |
| Decision | **BLOCKED** |
| Release approval | Not granted |
| Evidence state | No real browser, SFU, TURN, or load evidence has been recorded |
| Unblock condition | A complete, provenance-checked run must satisfy every required scenario and threshold |

This document is the report contract for the fanout benchmark. It is not proof
that the benchmark passed. Empty fields, unknown values, incomplete provenance,
synthetic browser results, or a failed tier keep the gate blocked.

## Benchmark objective

The benchmark determines a conservative, environment-specific receiver ceiling
without treating protocol simulation as browser evidence. It measures the
receiver tiers `10`, `25`, `50`, `100`, and `250` in ascending order.

Results from these paths are never pooled:

- SFU direct-path fanout, with no TURN relay in the measured media path.
- All-TURN fanout, where every measured media flow is relayed.
- Direct peer-pair regression baseline, reported independently and never used
  to raise the SFU fanout ceiling.

The required workload matrix covers one and multiple publishers, camera,
screen share, audio, transcript, semantic broadcast, private recovery, ordinary
fallback, and the pairwise combinations selected by the checked-in scale
profile. Any omitted required cell makes the run incomplete.

## Evidence boundary

A benchmark result is admissible only when it originates from real processes
and contains the raw measurements needed to recompute its verdict. A load
client may generate protocol-faithful traffic, but it does not count as a real
browser sentinel.

At least three real browser sentinels must participate at every receiver tier.
They calibrate load-client observations against browser CPU, received bytes,
packet counts, selected layers, end-to-end latency, rekey latency, and recovery
latency. Missing calibration, divergent counters without explanation, or a
sentinel that did not carry real media blocks the tier.

The report must preserve the exact command line and the immutable input and
image digests supplied by the execution environment. No identifier, digest,
measurement, or success result may be filled with a placeholder that resembles
real evidence.

## Required environment record

Every repetition must bind its measurements to the following environment. A
missing or unknown value blocks approval.

| Area | Required record |
| --- | --- |
| Hardware | Host model, CPU model and count, memory size, storage relevant to logging |
| Kernel | Kernel release, relevant network/sysctl settings, clock source |
| NIC | Interface model, driver, link rate, MTU, offload settings, queue configuration |
| Placement | Region, availability zone or failure domain, measured network topology |
| Containers | Runtime and version, image digests, CPU/memory limits, file-descriptor limits |
| SFU | Implementation version, image digest, configuration digest, resource limits |
| TURN | Implementation version, image digest, configuration digest, relay pool shape |
| Hub | Image digest, configuration digest, orchestration topology |
| Load system | Load-client image and configuration digests, placement, process counts |
| Browsers | Browser engine and version for every real sentinel, host/container placement |
| Inputs | Scale profile digest, schema digest, source digest, infrastructure digest |

## Required execution record

| Field | Requirement | Recorded value |
| --- | --- | --- |
| Receiver tiers | `10`, `25`, `50`, `100`, `250` in ascending order | Not run |
| Warmup | Duration from the checked-in profile | Not run |
| Measurement | Duration from the checked-in profile | Not run |
| Repetitions | Count from the checked-in profile | Not run |
| Confidence | Method and confidence level from the profile | Not run |
| Variance | Method, bound, and observed value per metric | Not run |
| Retry policy | Maximum attempts and reasons for every retry | Not run |
| Random seed | Explicit seed shared by plan and result | Not run |
| Browser sentinels | At least three real sentinels per tier | Not run |
| First failed tier | Tier and failure reasons, if any | Not run |

Retries never erase a failed attempt. Every attempt and its reason remain in the
evidence bundle. An infrastructure error is not silently reclassified as a
passing benchmark.

## Measurement sequence

1. Validate the scale profile and all required environment records.
2. Prove that SFU direct-path and all-TURN routing match their declared modes.
3. Calibrate protocol-faithful clients against at least three real browser sentinels.
4. Run the configured warmup without including warmup samples in the verdict.
5. Measure each required scenario for the configured duration and repetitions.
6. Evaluate variance and confidence using the configured methods.
7. Stop approval at the first failed receiver tier.
8. Preserve higher-tier observations only as diagnostics; they cannot restore approval.

The first failed tier is a hard approval boundary. A later passing tier, a
rerun with relaxed thresholds, or an average across transport modes cannot
override it. A new attempt requires a new complete evidence bundle.

## Required metrics

The report records distributions and the raw aggregation inputs, not only a
single summary value:

- Publisher ingress and receiver egress bytes, packets, and bitrate.
- Hub, SFU, TURN, browser-sentinel, and load-client CPU and memory.
- File descriptors, sockets, queues, relay allocations, and port pressure.
- Packet loss, retransmission, jitter, and latency percentiles.
- Join, publish, subscribe, rekey, layer-switch, and recovery latency.
- Queue depth, dropped work, retry counts, and retry rate.
- Selected spatial/temporal layers and layer-change causes.
- Transcript, semantic, private-recovery, and ordinary-fallback delivery results.
- Shared-resource peaks and per-room/per-receiver accounting reconciliation.

All-TURN results additionally include actual relay amplification and reconcile
TURN, SFU, and hub accounting. Direct-path results must prove that TURN traffic
was absent from the measured media path. Unexplained accounting gaps block the
corresponding tier.

## Tier result table

| Receivers | SFU direct path | All-TURN | Browser calibration | Variance/confidence | Approval |
| ---: | --- | --- | --- | --- | --- |
| 10 | Not run | Not run | Missing | Missing | Blocked |
| 25 | Not run | Not run | Missing | Missing | Blocked |
| 50 | Not run | Not run | Missing | Missing | Blocked |
| 100 | Not run | Not run | Missing | Missing | Blocked |
| 250 | Not run | Not run | Missing | Missing | Blocked |

## Fail-closed approval rules

The gate remains blocked when any of the following is true:

- Real browser, SFU, TURN, hub, or load-process attestation is absent.
- A required matrix cell, tier, metric, digest, or environment field is absent.
- Direct-path and all-TURN data cannot be distinguished and reconciled.
- Browser calibration is missing, incomplete, or outside the configured bound.
- A threshold fails, variance exceeds its bound, or confidence is insufficient.
- Resource exhaustion causes an uncontrolled crash, OOM, retry storm, or silent loss.
- Privacy, authorization, E2EE, stale-access, duplicate, orphan, or parent gates regress.
- The first failed tier is ignored or a higher tier is used to approve capacity.
- The evidence has been edited, is stale, or cannot be reproduced from its digests.

Capacity derivation consumes only a completed, passing, provenance-checked
report. It must subtract the configured reserve and may produce a lower ceiling;
it must never reinterpret this blocked template as measured evidence.

## Current conclusion

`SFB-GATE-008` is **BLOCKED**. No receiver tier has been executed with the
required real browser sentinels, transport separation, environment binding, or
repeatable measurements.
