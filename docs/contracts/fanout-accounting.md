# Fanout accounting window contract v1

## Scope and authority

`fanout_accounting_window.v1.json` defines a content-free observation emitted
by an SFU node and accepted by the Hub. It does not authorize a receiver,
publication, layer, route, key epoch, placement decision, quota increase or
traffic class. The Hub remains the owner of routing, policy, reconciliation
and the durable merge state. A node only reports work it already performed.

The contract is additive. It reuses canonical references from
`sfu_broadcast_extension_defs.v1.json` and directly specializes
`secure_envelope.v1.json`. It deliberately does not define another envelope,
signature algorithm, key format or trust store.

The required signature binding means that the boundary has authenticated the
Parent Envelope and verified its trusted sender/key association. Every field
under `accounting`, including the window and its sequence, must match the
authenticated plaintext exactly. An envelope that merely accompanies an
unbound clear-text object is invalid.

## Closed dimensions and SI units

Every variable value is an integer. Fractions, numeric strings, NaN and
Infinity are invalid. Counter values are non-negative and cannot exceed
`9007199254740991` either individually or after a checked merge.

| Path | Unit and meaning |
|---|---|
| `publisher_ingress.bytes` | bytes received for the publication at the SFU |
| `publisher_ingress.packets` | packets received for the publication at the SFU |
| `sfu_egress.bytes` | bytes emitted by the SFU for the publication |
| `sfu_egress.packets` | packets emitted by the SFU for the publication |
| `turn_relay.*_bytes` | bytes observed at the TURN boundary by direction |
| `turn_relay.*_packets` | packets observed at the TURN boundary by direction |
| `direct_traffic.*_bytes` | bytes on non-TURN paths by direction |
| `direct_traffic.*_packets` | packets on non-TURN paths by direction |
| `receiver_classes.*.receiver_count` | receivers in the fixed class during the window |
| `receiver_classes.*.egress_bytes` | SFU egress bytes attributed to the fixed class |
| `receiver_classes.*.egress_packets` | SFU egress packets attributed to the fixed class |
| `layers.*.egress_bytes` | SFU egress bytes attributed to the fixed layer bucket |
| `layers.*.egress_packets` | SFU egress packets attributed to the fixed layer bucket |
| `drops.*_packets` | dropped packets for the fixed reason |
| `queue.wait_time_sum_ms` | sum of sampled queue wait durations in milliseconds |
| `queue.wait_time_max_ms` | maximum sampled queue wait in milliseconds |
| `queue.wait_samples` | number of queue wait samples |
| `queue.depth_peak_packets` | maximum queue depth in packets |
| `shared_processing.cpu_time_ns` | shared processing CPU time in nanoseconds |
| `shared_processing.work_items` | shared processing work items |

Receiver classes, layer buckets, drop reasons and estimate reasons are closed
objects/enums. Missing buckets are not inferred: producers report zero. This
keeps canonical merge behavior deterministic and prevents unbounded metric
cardinality.

The receiver classes are:

- `realtime_interactive`
- `broadcast_receive_only`
- `server_side_consumer`

Layer accounting uses `audio_s0_t0` and the nine fixed video buckets
`video_s{0..2}_t{0..2}`. These are accounting dimensions, not permission or
requested/effective-layer declarations.

## Binding and time model

One report binds exactly one tenant, producing node, room, publication, route
and route epoch. A window has the half-open interval
`[started_at_ms, ended_at_ms)`, a fixed duration of 10,000 milliseconds and a
monotone accounting `sequence` within this exact binding.

The boundary MUST verify all of the following before merge:

1. `ended_at_ms - started_at_ms == duration_ms == 10000` using checked integer arithmetic.
2. `started_at_ms` is aligned to a 10,000 millisecond boundary.
3. The room and publication exist in the Hub-owned active route named by `route_ref`.
4. `route_epoch` equals the active route epoch; lower values are stale and higher values are unissued.
5. `node_ref` is the observed owner for this room at this epoch. It is never inferred before native placement is observed.
6. Envelope scope is the same room, envelope sender is the same node and recipient is the configured Hub accounting peer.
7. Envelope authentication succeeds with the trusted Parent-Envelope key binding, payload type is the contract domain and decrypted plaintext equals `accounting` canonically.
8. Envelope expiry/replay checks and accounting sequence checks both pass.

Envelope sequence and accounting sequence are deliberately separate.
Envelope sequence protects transport replay; accounting sequence orders source
windows. A retry can use a fresh envelope sequence while retaining the same
accounting sequence and body.

## Deterministic and idempotent merge

The merge identity is the tuple:

`(tenant_ref, node_ref, room_ref, publication_ref, route_ref, route_epoch, started_at_ms, ended_at_ms, sequence)`

The Hub stores that identity together with a digest of the authenticated,
canonical `accounting` object and the checked aggregate result. The digest is
computed by the Hub and is not a caller-controlled label.

Merge behavior is atomic:

1. Authenticate and bind the Parent Envelope.
2. If the full merge identity already exists and its canonical accounting body is byte-equivalent, return `accounting_idempotent_noop`; do not increment aggregates.
3. If the identity or sequence exists with a different window/body, reject `accounting_duplicate_sequence_conflict`.
4. Reject any non-identical half-open window intersecting an accepted window for the same binding and epoch with `accounting_window_overlap`.
5. Reject a sequence lower than the latest accepted non-duplicate sequence with `accounting_sequence_regression`.
6. A forward sequence gap is accepted only when quality is `estimated` with `counter_source_gap`; otherwise reject `accounting_sequence_gap_unmarked`.
7. Add every leaf counter with checked unsigned arithmetic in one transaction. Any overflow rejects the whole merge with `accounting_counter_overflow`.

No partial aggregate, queue publication, quota mutation or routing side effect
may occur on a rejected record. Durable merge state belongs to the Hub store;
container-local memory is only a cache and is never authoritative.

## Plausibility and estimates

The Hub resolves the effective named limit
`ananta.sfu-broadcast.accounting-egress-receiver-ratio-basis-points.max.v1`,
which cannot exceed the schema hard bound of 40,000 basis points. It computes:

`sfu_egress.bytes * 10000 / (publisher_ingress.bytes * authoritative_receiver_count)`

with checked multiplication/division. When the denominator is zero and egress
is non-zero, the ratio is unplausible. The authoritative receiver count comes
from Hub route/audience state, never from the report.

An unplausible ratio is not silently treated as measured. The producer must
sign `quality.status = estimated` and include
`egress_receiver_ratio_unplausible`; otherwise the boundary rejects
`accounting_unplausible_egress_receiver_ratio_unmarked`. Fixed reason codes
also mark source gaps, TURN reconciliation mismatches, receiver-class or layer
reconciliation mismatches and partial queue sampling. A measured report has an
empty reason list. Unknown reason strings are invalid.

Marked estimates remain observations. They cannot relax traffic budgets,
capacity admission or receiver rights. Consumers must preserve the quality
marker in rollups.

## Validation order and stable codes

Validation is fail-closed in this order so ambiguous records have a stable
result:

| Phase | Code |
|---|---|
| parse/size | `accounting_report_bytes_hard_limit_exceeded` |
| effective limit | `accounting_effective_limit_exceeded` |
| schema/unknown field | `accounting_schema_invalid` |
| negative counter | `accounting_counter_regression` |
| counter above hard/effective bound or checked sum overflow | `accounting_counter_overflow` |
| Parent Envelope authentication | `accounting_signature_invalid` |
| decrypted/plaintext mismatch | `accounting_signature_binding_mismatch` |
| sender/node mismatch | `accounting_node_signature_mismatch` |
| room mismatch | `accounting_cross_room` |
| publication mismatch | `accounting_cross_publication` |
| route mismatch | `accounting_cross_route` |
| stale/unissued route epoch | `accounting_stale_route_epoch` / `accounting_route_epoch_unissued` |
| invalid duration/alignment/order | `accounting_window_invalid` |
| exact authenticated retry | `accounting_idempotent_noop` |
| reused sequence with changed data | `accounting_duplicate_sequence_conflict` |
| intersecting interval | `accounting_window_overlap` |
| lower sequence | `accounting_sequence_regression` |
| unmarked sequence gap | `accounting_sequence_gap_unmarked` |
| unmarked egress ratio | `accounting_unplausible_egress_receiver_ratio_unmarked` |
| accepted measurement/estimate | `ok` / `accounting_estimate_accepted` |

Schema-negative counter values map to `accounting_counter_regression` rather
than the generic schema code. Values above the counter bound map to
`accounting_counter_overflow`. This explicit preclassification makes the
required failure behavior stable without weakening the JSON Schema.

## Privacy and security boundary

The accounting object accepts no media/data payload, ciphertext, transcript,
SDP, embedding, original participant or subscriber identifier, IP address,
device label, token, secret, key material, arbitrary tag map or free-form
label. The only node, room, publication and route references are bounded
control-plane pseudonyms needed for merge scope.

`key_id`, nonce and ciphertext occur only in the canonical Parent Envelope;
accounting counters cannot duplicate them. Logs and metrics must expose stable
reason codes and bounded references, never decrypted envelope material.

The contract's focused responsibility protects SRP. Canonical reference and
envelope dependencies protect DIP and avoid a second security authority.
Closed dimensions prevent a broad telemetry interface and preserve ISP. No
worker-to-worker task or accounting flow is introduced.

## Deterministic fixtures and evidence status

Fixtures are under
`tests/fixtures/webrtc/fanout_accounting_window/`. A base fixture contains
validation context, instance and expected result. Derived fixtures apply one
`mutation` or an ordered `mutations` array to either `instance` (default) or
the explicitly named target. Test context is not production evidence.

The manifest intentionally contains no invented `SRC_*` or `RUN_*` values.
The fixtures prove deterministic contract behavior only; they do not prove a
production rollout, capacity result or runtime activation. Activation remains
fail-closed until separately supplied evidence is verified.
