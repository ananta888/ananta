# SFU broadcast extension rules

## Scope and authority

`schemas/webrtc/sfu_broadcast_extension_defs.v1.json` is a definitions module
for additive SFU broadcast contracts. It is not a second security envelope and
it is not an authority for membership, publication, subscription, consent,
roles, keys, or cryptographic algorithms.

The repository-resolved parent artifact is:

- Local path: `schemas/webrtc/secure_envelope.v1.json`
- Canonical `$id`: `https://ananta.local/schemas/webrtc/secure_envelope.v1.json`
- Required direct `$ref`:
  `https://ananta.local/schemas/webrtc/secure_envelope.v1.json`

No `SRC_*` or `RUN_*` identifier was supplied for the parent task state. This
document therefore does not claim that `ASMP-SEC-003` or any related parent
task is complete. The repository artifact is resolved, while its external
provenance and operational deployment remain unverified. A resolver that
cannot load the exact `$id`, or resolves another version, must reject the
extension with `parent_contract_unavailable`; it must not substitute a local
envelope.

## Allowed shared definitions

The module exposes only these reusable contract concepts:

| Concept | Definitions | Meaning |
| --- | --- | --- |
| References | `tenant_ref`, `room_ref`, `publication_ref`, `subscription_ref` | Opaque identifiers for an authority-owned record. They never embed that record. |
| Epochs | `membership_epoch`, `key_epoch`, `route_epoch`, `topology_epoch` | Positive, bounded fencing counters. They do not carry membership, key, route, or topology data. |
| Limits | `extension_payload_bytes_limit_ref`, `fanout_count_limit_ref`, `extension_ttl_limit_ref` | Stable names resolved by the hub's authoritative limit policy. They are not sender-selected values. |
| Domain | `domain_separation` | A versioned `sfu_broadcast.<name>.v1` discriminator constrained by the parent `payload_type` boundary. |
| Parent boundary | `fanout_envelope` | The direct canonical parent `$ref` plus a narrower domain constraint. |

The root `conformance_profile` is a closed boundary-test witness. It is not a
wire payload base class. Concrete fanout schemas reference the individual
definitions they need and include their own direct parent-envelope `$ref`.
This avoids inheritance coupling and lets each concrete schema stay closed to
unknown properties.

## Parent-envelope invariants

Every concrete fanout schema must reference the exact canonical parent
envelope. It may narrow parent fields with `const`, `enum`, `allOf`, or stricter
numeric/string bounds. It must not copy or relax the parent properties.
Signature/authentication, sequence, replay, expiry, nonce, recipient, key ID,
AAD, and ciphertext validation remain exclusively governed by the parent
contract and its validator.

In particular, an extension must not add any of the following as a parallel
source of truth:

- role, permission, membership, or consent documents
- publication or subscription records
- key material, key packages, key selection rules, or algorithm identifiers
- signature, replay, sequence, nonce, or expiry alternatives

The parent's `additionalProperties: false` boundary applies unchanged.
Unknown security properties are rejected rather than ignored.

## Reference rules

References are identifiers only. The hub resolves them against its
tenant-scoped authoritative stores. A syntactically valid reference does not
grant access and does not prove that the referenced entity exists. A worker or
client must never resolve a reference into new authorization state.

Every extension requires `tenant_ref` and `room_ref`. A concrete schema makes
`publication_ref` and/or `subscription_ref` required only when its payload is
scoped to those records. The parent envelope's room scope must equal
`references.room_ref`; a mismatch is rejected before dispatch.

## Epoch rules

JSON Schema can bound a single epoch but cannot compare it with authoritative
persisted state. The hub-side boundary validator therefore performs the
monotonic check for each reference scope:

1. Resolve the last accepted epoch set from hub-owned state.
2. Reject any lower membership, key, route, or topology epoch with
   `epoch_regression`.
3. Require `envelope.epoch` to equal `epochs.key_epoch`.
4. Commit an accepted higher epoch and the associated operation atomically.

An unavailable baseline for an existing scope fails closed. Only the hub's
authoritative create transition may establish the first baseline. Workers do
not advance epochs and do not coordinate epoch state with other workers.

## Named limit rules

The schema deliberately contains stable limit names, not deployment values:

- `ananta.sfu-broadcast.extension-payload-bytes.max.v1`
- `ananta.sfu-broadcast.fanout-count.max.v1`
- `ananta.sfu-broadcast.extension-ttl-ms.max.v1`

The hub resolves all three names from the authoritative measured-budget
policy before accepting an extension. Missing, unknown, non-positive, stale,
or tenant-inapplicable resolutions fail with `limit_unresolved`. The effective
boundary is always the strictest of the canonical parent bound, the concrete
schema bound, and the resolved named limit. An extension cannot raise a parent
bound.

`extension_payload_bytes_max` applies to the serialized extension plaintext
before parent-envelope encryption. `fanout_count_max` applies to the concrete
schema's bounded fanout collection. `extension_ttl_ms_max` applies to
`envelope.expires_at_ms - validation_now_ms`. Expiry is still carried and
validated only by the parent envelope. `expires_at_ms <= validation_now_ms`
is rejected with `parent_envelope_expired`.

## Domain separation

A concrete schema chooses one versioned constant matching
`sfu_broadcast.<name>.v1`. The same constant is required in both its domain
field and the parent envelope's `payload_type`. The hub rejects a missing,
cross-contract, differently versioned, or merely pattern-compatible mismatch
with `domain_separation_mismatch`.

The concrete contract digest remains in the parent envelope's AAD and follows
the parent validation rules. The extension neither redefines the digest nor
introduces a second domain field.

## Concrete schema requirements

A specialized fanout schema is conformant only when all of the following hold:

1. Its envelope includes the exact direct parent `$ref` listed above.
2. Its envelope narrows `payload_type` to the concrete domain constant.
3. Its references and epochs use `$ref` entries from this definitions module.
4. Its relevant arrays and payloads enforce the resolved named limits at the
   hub boundary.
5. All objects are closed to unknown properties.
6. It adds no alternate security or authorization authority.

Schema inspection must reject a changed or indirect parent version with
`parent_envelope_ref_mismatch`. This check is additive to normal JSON Schema
resolution and prevents an unavailable version from being silently accepted.

## Deterministic boundary fixtures

Fixtures live in
`tests/fixtures/webrtc/sfu_broadcast_extension_defs/`. The valid fixture holds
a conformance-profile instance plus deterministic validation context. Negative
fixtures reference that base and describe one mutation. JSON-pointer mutations
target the base `instance` unless `target` says `schema` or
`validation_context`. `replace_with_repeated_string` creates the stated ASCII
character count without committing a large generated blob.

These are extension-boundary fixtures, not new cryptographic test vectors.
Authentication and decryption vectors remain owned by the canonical parent
suite. The cases cover unknown security properties, inherited parent
oversize, expiry, epoch regression, wrong parent `$ref`, and domain mismatch.
