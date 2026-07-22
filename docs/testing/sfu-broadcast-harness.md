# SFU broadcast gate harness

## Scope

The broadcast harness extends the existing Semantic-SFU harness. It does not
replace `scripts/run_semantic_sfu_gate.py`, the group E2E runner, or the
failover runner. The shared profile binds their paths together with the
broadcast-only local fuzz, security, and advanced-browser evidence boundaries.

The gate foundation deliberately separates three evidence classes:

- `local_model`: schema, validator, state-machine, queue, adapter-contract, and security tests without a real media plane.
- `external_real_media`: isolated Hub, database, pinned SFU/TURN containers, real browser processes, packet capture, fault injection, and cleanup evidence.
- `blocked`: required evidence was not supplied or could not be verified.

A local model or Playwright mock can never satisfy an external real-media
claim. Playwright WebKit and mobile viewports are not Safari or iOS device
evidence.

## Versioned profile

`config/test-profiles/sfu-broadcast/acceptance.v1.json` fixes:

- deterministic seeds and minimum cases per seed;
- CPU, memory, wall-clock, artifact, process, and cleanup bounds;
- pinned LiveKit and coturn image digests;
- browser lockfile and infrastructure inputs;
- the complete reversible fault catalog;
- required cleanup observations.

The default profile remains at the conservative participant cap. Capacity
test tiers are not acceptance claims.

## Local gates

The fuzz gate covers malformed contracts, Unicode and integer boundaries,
state transition order, replay, epoch/fence regression, signature mutation,
queue floods, priority inversion, and blocked receivers. It records only
seeds, coverage names, digests, content-free reason codes, configured bounds,
and OS resource measurements.

```bash
python scripts/run_sfu_broadcast_fuzz_gate.py
```

The security gate combines local adversarial tests with the existing bounded
privacy sentinel scanner. Without a manifest covering Hub, SFU, TURN, browser,
metrics, trace, crashdump, and test artifacts plus validated external media
evidence, its result is `blocked`.

```bash
python scripts/run_sfu_broadcast_security_gate.py \
  --privacy-root <ephemeral-run-root> \
  --privacy-manifest <content-free-manifest.json> \
  --sentinels <ephemeral-sentinels.json> \
  --external-result <real-media-result.json>
```

Do not commit sentinel values, packet captures, credentials, browser logs, or
other per-run raw artifacts.

## External adapter contract

`scripts/e2e/sfu_broadcast_harness.py` validates an externally produced
`ananta.sfu-broadcast-real-media-result.v1` document. The result must bind the
source, schema, config, browser lock, image, and infrastructure digests; the
selected seed; timestamps; all reversible faults; real-process/no-mock
attestation; media measurements; privacy scan; and cleanup counters.

The real runner must prove one publisher, at least three concurrent receivers,
one upstream peer connection and publication per source, multiple observed
RIDs or an approved SVC mode, independent receiver changes, and zero private
recovery cross-receiver matches.

All owned tracks, peer connections, rooms, listeners, timers, child processes,
ports, containers, routes, allocations, and leases must be gone by
`cleanup_deadline_ms`; credentials must be invalidated or expired.

## Current blockers

- No selected broadcast-capable LiveKit adapter has supplied contract evidence.
- No real advanced-browser/container/media run has been supplied.
- Safari, iOS, and Android require external device evidence.
- Duplicate JSON keys are not yet rejected by the current contract decoder;
  the fuzz gate reports `duplicate_json_key_not_rejected` and blocks.
- Packet/sentinel evidence for all required surfaces remains external.

No `SRC_*` or `RUN_*` identifier is generated. Evidence is bound by repository
paths, canonical digests, versioned profiles, and externally measured facts.

