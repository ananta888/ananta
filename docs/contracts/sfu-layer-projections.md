# SFU layer projection contracts

## Scope and authority

The four v1 contracts separate session construction, publisher encoding,
receiver selection, and browser acknowledgement. The hub creates and validates
every projection from one atomic snapshot of its authoritative state. Workers
may execute only delegated projection work; neither a worker nor a browser may
create tasks, coordinate another worker, or turn a reference into authority.

| Contract | Single responsibility |
| --- | --- |
| `sfu_room_session_projection.v1.json` | Fix topology, infrastructure profile, and the one layer-control mode before room construction. |
| `sfu_publisher_layer_projection.v1.json` | Narrow one existing publication intent to a bounded encoding plan. |
| `sfu_receiver_layer_projection.v1.json` | Narrow one existing subscription to a subscriber-specific layer corridor. |
| `sfu_layer_projection_receipt.v1.json` | Report the bounded application result without becoming authoritative. |

All objects are closed. Every `tenant_ref`, `room_ref`, publication,
subscription, and epoch is an opaque reference to hub-owned state. Syntactic
validity never creates membership, consent, publication, subscription, cap,
key epoch, route, or topology. Publisher and receiver projections have
`authorization_effect: narrow_only`; the room projection and receipt have
`authorization_effect: none`. Effective rights are always the intersection
with current hub authority. Empty intersection is `deny`.

No `SRC_*` or `RUN_*` evidence was supplied for browser, LiveKit, codec, or
runtime behavior. Consequently these schemas define only Ananta policy
vocabularies. They make no claim that a browser or infrastructure profile
implements a listed mode, codec, RID, or scalability class. Missing evidence
is `unknown` and follows a safe outcome; it is never inferred as support.

## Canonical dependencies and envelope

Each schema directly references the repository-resolved parent contract at
`https://ananta.local/schemas/webrtc/secure_envelope.v1.json`. It narrows the
parent payload type and control AAD but does not define another signature,
sequence, nonce, algorithm, key, replay, or expiry mechanism. The v1 parent
uses authenticated ciphertext rather than a standalone `signature` property;
a modified signature/tag/ciphertext is therefore reported uniformly as
`parent_authentication_failed` by the parent validator.

Common identifiers, epochs, and named limits reference
`sfu_broadcast_extension_defs.v1.json`. Layer selections reference the closed
CON-004 definition in `receiver_quality_observation.v1.json`. A projection
may carry at most one coarse CON-011 capability bucket, referenced from
`browser_media_capability_observation.v1.json`; raw API output is forbidden.
Failure to resolve any exact canonical `$id` fails closed with
`dependency_contract_unavailable`. A changed parent `$ref` fails contract
inspection with `parent_envelope_ref_mismatch`.

The parent envelope scope ID must equal `room_ref`, its key epoch must equal
`key_epoch`, its payload type must equal the projection domain, its recipient
must equal the intended browser or hub, and its authenticated contract digest
must identify the exact schema version. These are contextual and cryptographic
checks in addition to JSON Schema validation.

## Room-session projection

The room projection is mandatory before construction for publishers and
receive-only participants. A client must obtain, authenticate, context-check,
and apply it before constructing the LiveKit room. Absence, expiry, unknown
profile, or failed verification means the SFU room is not constructed.

`layer_control_mode` is the closed set `adaptive_stream` and
`manual_quality`. It is immutable for the lifetime of a constructed room
session. The session owner and `owner_generation` are fenced too. Changing
the owner, topology, infrastructure profile, or layer-control mode is not a
track update or subscription mutation. It requires a controlled disconnect
and reconnect, or a new session, with both a greater topology epoch where
topology ownership changed and a greater projection version. The old owner is
fenced before the replacement becomes effective.

The conservative baseline is spatial 0, temporal 0. For unknown or unsupported
capability the hub chooses only `ordinary_fallback` or `deny`. This baseline
does not itself grant a track or subscription.

## Publisher projection

A publisher projection resolves exactly one existing publication intent and
active room-session projection. A planned projection carries one to three
closed encoding entries. Audio is Opus with no RID or scalability layer.
Video and screenshare use closed codec, encoding, RID, and scalability
classes, plus hard per-encoding bitrate, dimension, FPS, spatial, and temporal
bounds. The aggregate plan must also remain within `publisher-hard-bounds-v1`.
Duplicate RID classes, a media-kind mismatch, or any bound excess is rejected
before browser or SFU allocation.

`unknown` and `unsupported` resolutions require an empty encoding plan and
only `ordinary_fallback` or `deny`. A projection cannot publish a track, add
a codec, or raise a cap absent the independently authorized publication
intent and current admission.

## Receiver projection

A receiver projection binds one tenant, room, subscriber, browser pseudonym,
subscription, and publication. The hub intersects the requested decision
with live membership, consent, subscription, publication, cap, key, route,
and topology state. `allowed_layer_corridor.minimum` must not exceed
`maximum`, and `effective_layer` must lie inside both axes. JSON Schema bounds
the axes; the hub performs these relational checks atomically.

The projection binds `coarse-v1` capability evidence and `bounded-v1` quality
evidence without copying raw observations. The fixed hysteresis policy needs
three consecutive observations to upgrade and two to downgrade, with a
3000-ms dwell and 5000-ms cooldown. Those constants bound churn; they are not
claims about vendor behavior. With unknown or unsupported evidence, only the
corridor minimum, ordinary fallback, or deny is valid. Ordinary fallback and
deny carry no effective SFU layer.

## Application receipt and privacy

A receipt acknowledges one receiver projection and exact subscriber,
subscription, publication, room-scoped browser pseudonym, epochs, and
monotone sequence. `applied` carries one closed codec, encoding, RID,
scalability, and layer value. `unsupported`, `fallback`, and `denied` carry
null application fields plus a closed reason code.

The receipt is never delivery proof, authorization evidence, or a quality
observation. Recursive pre-scan rejects WebRTC stats, SDP, ICE, IP addresses,
device identifiers or labels, hardware/browser fingerprints, media bytes,
frames, audio, transcripts, embeddings, and arbitrary diagnostic text.
Accepted receipts remain volatile and retain at most eight entries for the
active subscription scope. They are purged at the earliest of receipt expiry,
pseudonym rotation, membership loss, subscription revocation, publication
end, room leave, or epoch replacement.

## Validation order and pre-allocation limits

The hub boundary applies this order before constructing a room, allocating an
encoding, changing a subscription, or retaining a receipt:

1. Measure received UTF-8 bytes and enforce the strictest parent, named, and concrete byte limit.
2. Privacy-scan receipts and reject known authority-expansion fields before generic schema errors.
3. Resolve exact schema dependencies, inspect the direct parent `$ref`, and validate the closed JSON Schema.
4. Authenticate the parent envelope and require domain, digest, scope, recipient, key epoch, and expiry equality.
5. Resolve exact tenant, room, admission, membership, publication, subscription, and subscriber state from one hub snapshot.
6. Require exact issued epochs. Lower is stale; higher is unissued. No client or worker advances an epoch.
7. Require the active session, publisher, capability, quality, and bounds profile versions to match exactly.
8. Apply fencing CAS: token and expected previous projection version must match current hub state.
9. Enforce TTL, encoding/layer/receipt-history/update-rate limits and all relational bounds.
10. Require both projection/envelope sequences to advance, then atomically persist acceptance state.

Retries with an equal or lower projection, receipt, or parent-envelope sequence
are replay. An unavailable state snapshot, unresolved limit, ambiguity, or
partial write fails closed. Reconciliation never broadens the last verified
intersection.

## Stable reason codes

| Code | Meaning |
| --- | --- |
| `ok` | Contract and context accepted. |
| `payload_bytes_exceeded` | Received plaintext exceeds the effective byte limit. |
| `unknown_field` | Property is outside a closed object. |
| `dependency_contract_unavailable` | Exact CON-001, CON-004, or CON-011 `$id` cannot be resolved. |
| `parent_envelope_ref_mismatch` | Schema does not directly reference secure-envelope v1. |
| `parent_authentication_failed` | Parent authentication/signature/tag/ciphertext verification failed. |
| `domain_separation_mismatch` | Domain, payload type, or contract digest identifies another contract. |
| `parent_envelope_expired` | Parent expiry has been reached. |
| `projection_ttl_exceeded` | Concrete TTL field or age exceeds its v1 bound. |
| `rights_expansion_forbidden` | Payload attempts to create or broaden authority. |
| `cross_tenant_projection` | Tenant differs from active authority. |
| `cross_room_projection` | Room or parent scope differs from active authority. |
| `cross_publication_projection` | Publication or intent differs from active authority. |
| `cross_subscription_projection` | Subscription differs from active authority. |
| `cross_subscriber_projection` | Subscriber or browser pseudonym differs from active authority. |
| `stale_admission_epoch` / `admission_epoch_unissued` | Admission epoch is lower/higher than active. |
| `stale_membership_epoch` / `membership_epoch_unissued` | Membership epoch is lower/higher than active. |
| `stale_route_epoch` / `route_epoch_unissued` | Route epoch is lower/higher than active. |
| `stale_topology_epoch` / `topology_epoch_unissued` | Topology epoch is lower/higher than active. |
| `stale_key_epoch` / `key_epoch_unissued` | Key epoch is lower/higher than active. |
| `stale_fencing_token` / `fencing_token_unissued` | Fencing token is lower/higher than current. |
| `projection_replay` | Projection, receipt, or envelope sequence did not advance. |
| `profile_version_conflict` | Session, publisher, receiver, bounds, capability, or quality version conflicts. |
| `session_projection_version_mismatch` | Child names a non-active room projection. |
| `owner_change_requires_reconnect` | Owner changed without the required disconnect/reconnect transition. |
| `layer_control_mode_locked` | A live session attempted a mode mutation. |
| `unsafe_fallback` | Unknown/unsupported state requested a non-safe outcome. |
| `encoding_count_exceeded` | Publisher plan exceeds three encodings. |
| `publisher_bound_exceeded` | Codec plan exceeds a hard bitrate/dimension/FPS/layer bound. |
| `duplicate_rid_class` | Publisher plan repeats a RID class. |
| `effective_layer_outside_corridor` | Effective or minimum layer is outside the authorized corridor. |
| `receipt_history_exceeded` | Acceptance would retain more than eight receipts. |
| `update_rate_exceeded` | Contract-specific rolling-minute update rate is exceeded. |
| `privacy_raw_stats_forbidden` | Receipt contains stats, SDP, ICE, IP, or raw diagnostics. |
| `privacy_device_forbidden` | Receipt contains device, hardware, or persistent client identity. |
| `privacy_media_forbidden` | Receipt contains media, transcript, embedding, frame, or payload content. |

## Deterministic fixtures

Fixtures in `tests/fixtures/webrtc/sfu_layer_projections/` contain full valid
instances or one deterministic JSON-Pointer mutation of a named valid base.
Context mutations exercise hub-owned comparisons that JSON Schema cannot
express. They cover both session modes, receive-only construction, publisher
and receiver bounds, safe unknown/unsupported behavior, and every negative
class named in the task: unknown field, oversize, stale epochs/fencing,
cross-room/publication/subscription/subscriber, replay, parent authentication
manipulation, and conflicting versions. Additional cases cover owner/mode
reconnect invariants, rights expansion, rate/history limits, and receipt
privacy.
