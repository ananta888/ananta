# SFU broadcast observability and privacy scan

## Scope

This document defines the operational boundary introduced by `SFB-OBS-001`
and `SFB-OBS-003`. It is an additive foundation. It does not activate SFU
broadcast, connect a real test harness, or constitute source-grounded release
evidence.

The hub remains the control plane. Workers and SFU nodes may emit observations,
but they do not select metrics, expand label vocabularies, change retention, or
make release decisions.

## Metric catalog

`config/sfu_broadcast_observability_catalog.json` is the versioned source of
truth. Its schema is
`schemas/webrtc/sfu_broadcast_observability_catalog.v1.json`. Each registered
metric declares:

- domain, purpose, metric type, unit and scope
- a closed label vocabulary and numerical buckets
- the fixed `p50`, `p95`, `p99` and `peak` window statistics
- aggregation window, minimum cohort and suppression rule
- maximum series and stored points per scope
- retention and pseudonym rotation periods
- query roles, query rate, maximum rows and export destination
- the `operational_aggregate` privacy class

`peak` is the maximum observation in a complete aggregation window. Percentiles
use nearest-rank selection. Empty and undersized windows are suppressed rather
than published as zero because zero publication permits differencing attacks.

The catalog covers join, publish, subscribe, group, route, layer, queue, drop,
ingress, SFU egress, TURN, rekey, drain, failover and capacity. It deliberately
does not expose participant, track, room, tenant, node or device identifiers.

## Policy enforcement

`SfuBroadcastObservabilityPolicy` accepts only a registered metric with the
exact registered labels and finite non-negative values. Unknown metrics,
unknown labels, forbidden identity/content labels, missing labels and values
outside a closed vocabulary are rejected with stable reason codes. Error text
never includes a rejected value.

The caller supplies a scope identifier only to the in-process policy boundary.
The policy derives `scope_pseudonym` with domain-separated HMAC-SHA-256,
truncated to 96 bits. The rotation epoch and metric scope are bound into the
derivation. The original identifier is neither returned nor included in the
series key. Secrets must contain at least 32 bytes and must come from the
runtime secret boundary, never from the catalog.

The following data is prohibited in metrics, logs, traces and scan reports:

- media or semantic payloads, transcripts and embeddings
- encryption keys, access tokens and credentials
- full SDP, ICE candidates and private IP addresses
- device labels and unmasked original identifiers

## Privacy sentinel scanner

`scripts/scan_sfu_broadcast_privacy_sentinels.py` scans only files explicitly
listed in a run-owned manifest. It performs no recursive discovery. A real
harness must inject a unique value for every category:

- `media`, `transcript`, `semantic`, `evidence`
- `key`, `token`, `credential`
- `private_ip`, `sdp_ice`

The manifest must cover hub, SFU, TURN, browser, metrics, trace, crashdump and
test-artifact surfaces. Every source has a declared format and required flag.
The limits `file_size_max` and `bytes_scanned_max` are additionally capped by
hard scanner limits. Files are read in bounded chunks with overlap, so a match
split across chunks is still detected.

The scanner detects raw sentinel values plus JSON-escaped, percent-encoded and
Base64 forms. Reversible encodings are not an allowlist. Only documented
one-way SHA-256 digests and HMAC pseudonyms are treated as safe controls because
the scanner does not expand them back into matching needles.

Run it only against a harness-owned directory:

```bash
python scripts/scan_sfu_broadcast_privacy_sentinels.py \
  --root /path/to/run-surfaces \
  --manifest /path/to/content-free-manifest.json \
  --sentinels /path/to/private-per-run-sentinels.json \
  --output artifacts/test-gates/sfu-broadcast-privacy-scan.json
```

The sentinel document is secret input. It must not be stored in artifacts,
logs, command output or source control. The report contains only source paths,
surface/category names, byte counts and stable reason codes. It contains no
matched bytes, snippets, offsets, sentinel hashes or timestamps.

## Fail-closed decisions

The scan decision is `block` when any of these conditions occurs:

- a sentinel is found
- a required source cannot be read
- a source is unsupported or resolves outside the scan root
- a file or global byte budget truncates coverage
- the manifest, sentinel set or scanner fails validation or execution

An `allow` result means only that the declared files were clean within complete
bounds. It is not release evidence by itself. `SFB-GATE-001` must later provide
real harness sources and valid externally supplied `SRC_*` and `RUN_*`
identifiers. Until that integration exists, no runtime or release claim may be
derived from this scanner.

## Remaining integration work

- Instrument hub, SFU, TURN and browser adapters through the catalog policy.
- Add authenticated operator query/read models and enforce catalog RBAC and
  query limits at that boundary.
- Connect all eight real run surfaces from the release harness.
- Store the generated report with valid source and run provenance in the gate
  evidence pipeline.
- Exercise retention deletion and exporter cardinality limits in a real 250
  receiver run.
