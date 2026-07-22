# Receiver group intent contract

## Scope and authority

`schemas/webrtc/receiver_group_intent.v1.json` defines a hub-owned desired-state
record. A group is only a projection of independently authorized participant
grants. Creating an intent does not create membership, consent, publication,
subscription, or key authority. Workers may consume a delegated projection,
but must not create groups, widen selectors, resolve grants, advance epochs, or
coordinate other workers.

The schema uses these repository-resolved parent definitions by canonical
`$id`:

| Parent | Reused definitions |
| --- | --- |
| `sfu_broadcast_extension_defs.v1.json` | Canonical IDs, tenant/room/publication references, epochs, and named limits |
| `media_publication.v1.json` | Privacy classification |
| `media_subscription.v1.json` | Subscription status and publication identifier |
| `capability_advertisement.v1.json` | Capability selector values |
| `group_key_epoch.v1.json` | SHA-256 digest representation |

No `SRC_*` or `RUN_*` identifier was supplied for external parent state. The
local `$id` targets are resolved repository artifacts; this contract makes no
claim that an external parent task or deployment is verified. Missing or
differently versioned `$ref` targets fail closed with
`parent_contract_unavailable`.

## Intent invariants

| Field | Invariant |
| --- | --- |
| `group_id` | Opaque ID minted by the hub. It is stable across revisions of one scope and is never derived from member identities or a digest. Reuse for another scope is rejected. |
| `revision` | Hub CAS revision. A replacement is exactly the persisted revision plus one. |
| `audience_snapshot_ref` | Reference to one immutable, hub-owned snapshot. It is not an inline or client-provided member list. |
| `scope` | Single tenant, room, consent, privacy, and publication scope for the whole group. |
| `epochs` | Exact membership, consent, key, and audience fences used to resolve the projection. |
| `member_count` | Derived snapshot count in the inclusive range zero through eight and additionally bounded by the resolved fanout limit. |
| `publications` | One primary publication, zero through seven explicitly allowed shared publications, and exactly one independently keyed digest per allowed publication. |
| `policy` | Immutable policy reference plus exact version. A newer authoritative version makes the intent stale. |
| `valid_from_ms` / `expires_at_ms` | Inclusive activation start and exclusive expiry. The hub supplies validation time. |
| `limits` | Stable policy names. They do not let the sender choose limit values. |

An empty group is valid as a fail-closed desired state for revocation and
reconciliation. It must not cause an SFU group or subscription to be created,
and it remains subject to revision, epoch, policy, and TTL checks. A group at
the schema maximum contains eight unique members. A lower resolved fanout
limit always wins.

Shared publications are an allowlist, not a wildcard. The primary reference
must not occur in `shared_publication_refs`; all references across both fields
must be unique. `member_digests` must contain exactly that same publication
set, once each. Every resolved member needs a separate active or authorized
subscription grant for every listed publication. Sharing cannot turn a grant
for one publication into a grant for another.

## Closed selector language

Selectors only narrow the already authorized audience snapshot. A matching
selector never grants access, and an empty selector list means no additional
narrowing. The only allowed clauses are:

| Kind | Canonical attribute | Operator | Value |
| --- | --- | --- | --- |
| `capability` | `/algorithms` | `contains` | Value accepted by the capability contract |
| `capability` | `/roles` | `contains` | Canonical capability role |
| `capability` | `/task_types` | `contains` | Canonical task type |
| `capability` | `/resource_profile/cpu` | `eq` | Canonical CPU class |
| `capability` | `/resource_profile/memory` | `eq` | Canonical memory class |
| `capability` | `/resource_profile/gpu` | `eq` | Canonical GPU class |
| `capability` | `/resource_profile/codec` | `eq` | Canonical codec class |
| `capability` | `/resource_profile/battery` | `eq` | Canonical battery class |
| `capability` | `/resource_profile/network` | `eq` | Canonical network class |
| `consent` | `/status` | `eq` | Only `granted` |
| `subscription` | `/status` | `in` | Non-empty subset of `authorized`, `active` |
| `subscription` | `/publication_id` | `eq` | Canonical publication reference |

Each clause and the intent itself are closed objects. Arbitrary attributes,
operators, expression trees, regexes, scripts, templates, callbacks, query
languages, and executable code are invalid. A selector engine implements this
finite table rather than evaluating caller-controlled text.

## Member digest

There is one `member_digests` entry for each allowed publication. The hub
computes each value as:

```text
HMAC-SHA-256(
  digest_key,
  UTF8("ananta.webrtc.receiver-group.member-digest.v1") || 0x00 ||
  JCS(digest_input)
)
```

`JCS` is RFC 8785 canonical JSON. `digest_input` is a closed object with these
keys:

```json
{
  "allowed_publication_refs": ["sorted primary and shared references"],
  "audience_epoch": 1,
  "audience_snapshot_ref": "opaque snapshot reference",
  "consent_epoch": 1,
  "consent_scope_ref": "opaque consent scope reference",
  "group_id": "stable group identifier",
  "key_epoch": 1,
  "member_bindings": [
    {
      "grant_ref": "opaque individual grant reference",
      "member_ref": "opaque member reference",
      "subscription_ref": "opaque per-publication subscription reference"
    }
  ],
  "membership_epoch": 1,
  "policy_ref": "opaque policy reference",
  "policy_version": 1,
  "privacy_scope": "ordinary",
  "publication_ref": "the digest entry publication",
  "publication_scope_ref": "opaque publication scope reference",
  "room_ref": "sfu room reference",
  "tenant_ref": "tenant reference"
}
```

Publication references and member bindings are sorted by unsigned UTF-8 byte
order before JCS encoding. Member bindings sort by `member_ref`, then
`grant_ref`, then `subscription_ref`. Duplicate members, grants,
subscriptions, or publication references are rejected before hashing.

The HMAC key is resolved only inside the hub/KMS boundary. Its registry tuple
is `(tenant_ref, room_ref, publication_ref, key_epoch,
receiver_group_member_digest)`. Every tuple has a separate random key and a
unique opaque `key_ref`. The key is never a media/E2EE key, never leaves hub
custody, and never appears in an intent. A key reference already registered
for another room, publication, epoch, tenant, or purpose is rejected rather
than rebound.

This keyed and scope-bound construction prevents an observer from hashing a
dictionary of likely participant identifiers and prevents digest comparison
across room, publication, or key-epoch boundaries. The digest is a
consistency commitment, not an authorization token or signature.

## Hub validation order

The hub validates and persists an intent in this order:

1. Validate the closed JSON Schema and resolve the exact canonical parents.
2. Resolve named limits, current time, policy version, and prior group revision.
3. Require `valid_from_ms < expires_at_ms`, `now_ms < expires_at_ms`, and a TTL no greater than the resolved limit.
4. Resolve the immutable audience snapshot and require its reference and audience epoch to match.
5. Reject duplicate members, grants, subscriptions, or publications before checking counts or digests.
6. Require the resolved unique count to equal `member_count` and not exceed either limit.
7. Resolve every member's individual grants, consent, capability advertisement, and subscriptions; apply selectors only as additional intersection filters.
8. Compare every resolved member and publication with the intent's tenant, room, consent scope, privacy scope, membership epoch, consent epoch, key epoch, and publication scope.
9. Require consent to be currently `granted`, subscriptions to be `authorized` or `active`, and all authoritative epochs and policy versions to equal the intent.
10. Resolve one hub-only non-media digest key for each publication, verify its registry tuple and uniqueness, rebuild the sorted digest input, and compare the HMAC in constant time.
11. CAS the stable `group_id` and next revision atomically with the snapshot, scope, epoch, publication, and digest bindings.

The first failure rejects the entire group. The hub does not split a
cross-scope audience into convenient subgroups, and a worker cannot repair or
override a rejection.

## Cross-scope rejection

All members and all listed publications must match every root scope boundary.
In particular, the hub rejects mixed tenants, rooms, consent scopes, privacy
classes, membership epochs, consent epochs, key epochs, and publication
scopes. It also rejects a member whose per-publication grant points outside
the explicit primary/shared allowlist. Missing authoritative data fails
closed; syntactic validity of an opaque reference proves neither existence nor
authorization.

Representative stable error codes are:

| Code | Meaning |
| --- | --- |
| `duplicate_member` | The resolved snapshot repeats a member. |
| `member_count_mismatch` | Derived unique count differs from `member_count`. |
| `member_digest_mismatch` | Constant-time HMAC comparison failed. |
| `stale_consent_epoch` | Intent or member consent is behind authoritative state. |
| `cross_tenant_member` / `cross_room_member` | A member crosses tenant or room scope. |
| `cross_consent_scope` / `cross_privacy_scope` | Consent or privacy scopes differ. |
| `cross_membership_epoch` / `cross_key_epoch` | A member crosses an epoch fence. |
| `cross_publication_scope` | A publication or grant crosses the explicit publication boundary. |
| `digest_key_scope_mismatch` | A digest key reference is reused across a room, publication, epoch, tenant, or purpose. |
| `selector_not_allowed` | A clause is outside the finite selector language. |
| `intent_expired` / `intent_ttl_exceeded` | Time bounds fail. |

## Deterministic fixtures

Fixtures live in
`tests/fixtures/webrtc/receiver_group_intent/`. A standalone positive fixture
contains `validation_context`, `instance`, and `expected`. Derived fixtures
name `valid_group.v1.json` in `base_fixture`; their single `mutation` or
ordered `mutations` apply JSON Pointer operations to the named target. The
wrapper is test metadata and is not part of the intent schema.

The positive set covers a normal group, an empty fail-closed group, and the
eight-member maximum. Negative cases cover duplicate membership, digest
tampering, stale consent, every cross-scope dimension, unkeyed dictionary
attempts, cross-room and cross-epoch key reuse, executable/unknown selectors,
expiry, and excessive TTL. Fixture HMAC material is explicitly test-only and
must never be used as a production key.
