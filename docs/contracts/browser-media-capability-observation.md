# Browser media capability observation contract

## Purpose and authority boundary

The v1 observation is a small, slowly changing hint from one admitted browser
instance to the hub. It is scoped to one tenant, one room, one active
admission, and one membership epoch. The hub may use an accepted observation
only as input to conservative media planning.

The observation is not an authorization document. Its required
authorization_effect is none. It cannot create or widen membership,
publication, subscription, layer, routing, topology, key, or consent rights.
The hub remains the sole control plane and compares the observation with its
authoritative admission state before use. Workers only consume work delegated
by the hub and never exchange or derive orchestration tasks from an
observation.

The schema is:

- Local path: schemas/webrtc/browser_media_capability_observation.v1.json
- Canonical id:
  https://ananta.local/schemas/webrtc/browser_media_capability_observation.v1.json
- Schema version: 1
- Capability vocabulary version: coarse-v1

No SRC_* or RUN_* identifier was supplied for browser or vendor behavior.
This contract therefore makes no source-grounded claim that a particular
browser implements a bucket. unknown is the required value when static
evidence is absent.

## Closed capability vocabulary

Each entry in capability_buckets is one coarse tuple. Every tuple is closed to
unknown properties and contains exactly these dimensions:

| Dimension | Allowed buckets |
| --- | --- |
| codec_bucket | unknown, unsupported, audio_opus, video_vp8, video_h264, video_vp9, video_av1 |
| layering_bucket | unknown, unsupported, simulcast, svc |
| encoded_transform_bucket | unknown, unsupported, available |
| decode_bucket | unknown, unsupported, audio_realtime, video_baseline, video_enhanced |
| evidence_bucket | not_observed, static_api_presence, static_capability_query |

Codec values identify only a codec family. Decode values identify only a
contract-defined qualitative query class; they do not claim a maximum,
concurrency level, hardware path, benchmark result, resolution, frame rate,
bit rate, or power characteristic. Layering values identify the coarse API
class only and never carry a layer count.

not_observed requires every capability dimension in that tuple to be unknown.
unsupported is a positive static result and therefore requires either
static_api_presence or static_capability_query. Known values must be produced
only from non-benchmark browser API presence or static capability queries.
The report carries the bucket result, never raw API output.

Audio Opus tuples can use only unknown or unsupported layering and can use
only audio decode buckets. Video codec tuples can use only video decode
buckets. A missing capability is represented by unknown rather than a new
field or a guessed value.

## Prohibited fingerprint and runtime data

The schema has closed objects at every level. In addition, the boundary
validator performs a recursive privacy pre-scan before generic unknown-field
reporting. The following data classes are always prohibited, including aliases
and nested forms:

- user-agent strings, browser build strings, platform strings, and locale lists
- CPU, GPU, memory, hardware concurrency, architecture, and hardware model data
- device IDs, labels, group IDs, inventory, sensor data, and permission-device mappings
- benchmark scores, timings, measured throughput, measured decode rate, and probe results
- SDP, ICE candidates, DTLS fingerprints, codec fmtp blobs, and raw RTP capability dumps
- IP, host, network-interface, TURN address, and geolocation values
- width, height, frame rate, bit rate, sample rate, channel count, media payloads, and quality statistics

Persistent account, installation, browser, session, analytics, or client IDs
are prohibited. The only browser identity accepted by this contract is the
hub-issued browser_instance_pseudonym.

max_encodings, max_layers, spatial-layer counts, temporal-layer counts, and
equivalent runtime limits are not part of this observation. Such values
require real, separately governed publication evidence and belong to a
publication-scoped contract. Adding them here is rejected even if the sender
also adds a publication reference.

## Privacy and lifetime limits

The four limit fields are versioned constants, not sender-selected budgets:

| Field | v1 value | Enforcement |
| --- | ---: | --- |
| capability_bucket_combinations_max | 8 | capability_buckets has at most eight unique tuples |
| report_bytes_max | 2048 | UTF-8 bytes of the received JSON report, checked before parsing |
| ttl_seconds | 300 | issued_at through issued_at plus 300 seconds |
| pseudonym_rotation_seconds | 900 | maximum age of the room-scoped pseudonym |

Changing a limit requires a new contract version. A sender cannot raise a
limit by editing the field because each value is constrained with const.

The hub issues a random, opaque room-bip_ pseudonym for exactly one
tenant_ref, room_ref, and admission_epoch. It contains no client identifier.
The hub rotates it no later than 900 seconds after issuance and on admission
replacement, room leave, or room change. The same pseudonym must not be
accepted in another room or tenant.

Accepted observations may be retained only in volatile state for the current
room admission. The hub purges them at the earliest of observation expiry,
pseudonym rotation, membership loss, room leave, or admission replacement.
They must not be copied to analytics, logs, profiles, cross-room stores, or
worker-local durable state and must not be correlated across pseudonym
rotations.

## Admission, epoch, time, and replay validation

JSON Schema validates shape and fixed limits. The hub-side boundary validator
owns contextual checks and applies them against one atomic snapshot of
hub-owned state:

1. Reject a report whose measured UTF-8 body exceeds report_bytes_max.
2. Recursively reject persistent identifiers, fingerprint fields, runtime
   limits, and authority claims before generic schema errors.
3. Validate the complete instance against the v1 JSON Schema.
4. Require tenant_ref and room_ref to equal the active admission scope.
5. Require an active admission and exact admission_epoch and membership_epoch
   equality. Lower epochs are stale; higher epochs are unissued and fail
   closed.
6. Require browser_instance_pseudonym to equal the active pseudonym for the
   same tenant, room, and admission and to be no older than
   pseudonym_rotation_seconds.
7. Parse issued_at as UTC, reject future timestamps, and require validation
   time to be no later than issued_at plus ttl_seconds.
8. Require sequence to be strictly greater than the last accepted sequence
   for the tenant, room, admission epoch, and pseudonym tuple.
9. Atomically store the accepted sequence and volatile observation, or reject
   both. No worker advances this state.

Sequence state resets only after the hub has issued a different pseudonym.
Retries with the same or lower sequence are replay attempts, even when their
payload differs.

## Stable reason codes

Validation uses the ordering above so a prohibited known privacy field is not
reported merely as an unknown property.

| Code | Meaning |
| --- | --- |
| ok | Observation accepted |
| report_bytes_exceeded | Received UTF-8 report exceeds 2048 bytes |
| persistent_client_id_forbidden | Persistent or cross-admission client identity is present |
| fingerprint_field_forbidden | UA, hardware, device, benchmark, SDP, IP, or raw media value is present |
| runtime_limit_without_publication_evidence | Encoding or layer maximum is present in this non-publication contract |
| authority_claim_forbidden | A publication, subscription, layer, topology, role, permission, key, or consent claim is present |
| unknown_field | A non-denylisted property is outside the closed schema |
| capability_entropy_exceeded | More than eight capability tuples are present |
| ttl_limit_exceeded | ttl_seconds differs from the v1 bound |
| pseudonym_rotation_limit_exceeded | pseudonym_rotation_seconds differs from the v1 bound |
| schema_invalid | Any other schema failure |
| cross_tenant_observation | tenant_ref differs from the active admission |
| cross_room_observation | room_ref differs from the active admission |
| inactive_admission | No active matching admission exists |
| stale_admission_epoch | admission_epoch is below the active epoch |
| admission_epoch_unissued | admission_epoch is above the active epoch |
| stale_membership_epoch | membership_epoch is below the active epoch |
| membership_epoch_unissued | membership_epoch is above the active epoch |
| pseudonym_scope_mismatch | Pseudonym is absent or not active for the exact admission scope |
| pseudonym_expired | Pseudonym has reached its rotation boundary |
| observation_from_future | issued_at is later than hub validation time |
| observation_expired | Observation has reached its TTL boundary |
| observation_replay | sequence is not strictly greater than the last accepted sequence |

## Deterministic fixtures

Fixtures live in
tests/fixtures/webrtc/browser_media_capability_observation/. Valid fixtures
contain validation_context, instance, and expected. Negative fixtures name a
valid base, mutate either instance or validation_context with a JSON Pointer,
and state the stable phase and reason code.

valid_unknown.v1.json proves the fail-closed unknown representation.
valid_partial.v1.json proves a mixed, statically evidenced partial report.
Negative fixtures cover byte oversize, unknown properties, excess entropy,
stale admission and membership epochs, replay, cross-tenant and cross-room
binding, persistent client identity, pseudonym lifetime, observation TTL,
every prohibited fingerprint-data class, ungrounded encoding/layer maxima,
and attempted authority expansion.
