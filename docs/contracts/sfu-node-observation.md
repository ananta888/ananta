# SFU node observation contract v1

## Purpose and authority boundary

`sfu_node_observation.v1` carries short-lived, authenticated observations from an SFU-facing probe into the hub. It is an evidence input for capability and health projection only.

The contract is deliberately non-authoritative. An accepted observation MUST NOT:

- select a placement target or override the runtime's native placement;
- admit a room, participant, publisher, or subscription;
- raise or lower a configured limit or quota;
- mutate routing, task ownership, or worker orchestration;
- be treated as durable inventory after it becomes stale.

Only the hub may combine a usable observation with independently configured policy. A numeric signal is telemetry, not capacity permission.

## Signed document and trusted ingress context

The schema in `schemas/webrtc/sfu_node_observation.v1.json` describes the signed wire document. The producer signs the domain-separation prefix `ananta.sfu-node-observation.v1\n` followed by the RFC 8785 canonical JSON bytes of the `observation` member. `proof` is not part of those signed bytes.

The ingress adapter MUST establish the following separately and MUST NOT copy self-asserted verification flags from the wire document:

- an encrypted TLS 1.2 or TLS 1.3 connection with a verified server peer;
- a verified client certificate for mTLS mode, including the authenticated peer SAN;
- an allowed `key_id` resolved from trusted configuration;
- a valid Ed25519 signature over the exact domain-separated canonical bytes;
- the local receipt time used for freshness and skew evaluation.

TLS without client authentication is permitted only when the signature key authenticates the producer. Plaintext, an unverified TLS peer, an unverified mTLS client, an unknown key, or an invalid signature is rejected fail-closed.

The deterministic fixtures wrap the wire document in a test-only object containing `context` and `expected`. Those fields model trusted ingress results and are not accepted wire fields. The signature strings are policy-harness placeholders, not cryptographic known-answer vectors; cryptographic verification tests must provide their own real key material.

## Scope modes

Exactly one scope shape is valid:

| Mode | Required key | Meaning |
|---|---|---|
| `producer` | `producer_id`, `runtime_id` | One authenticated publishing session on a runtime |
| `runtime` | `runtime_id` | One logical SFU runtime endpoint |
| `cluster` | `cluster_id` | An aggregate cluster view |
| `region` | `region_id` | An aggregate regional view |

Signal names, units, and measurement sources are fixed per mode by the schema. Cross-mode signal reuse is rejected instead of being guessed.

`node_id` is never a scope selector. It may occur only as `observed_node.node_id` on a `runtime` observation and only with one of these bindings:

- `mtls_san`: the trusted ingress SAN MUST exactly match `observed_node.binding.san`, and the authenticated SAN-to-node mapping MUST resolve to the same `node_id`;
- `signed_claim`: the accepted signing key MUST be authorized to claim that `node_id` for the scoped `runtime_id`.

A load-balancer hostname, source IP, cluster aggregate, region aggregate, or producer identifier is not node evidence. If the binding cannot be established, consumers MUST omit the node identity and project node-specific state as `unknown`.

## Capabilities

Capability names are a closed enum. Each entry carries a state and one fixed evidence class:

- `authenticated_runtime_probe`
- `signed_runtime_manifest`

Missing capabilities and explicit `state: unknown` both project to `unknown`. Unknown capability names are rejected so spelling errors and unreviewed runtime features cannot silently become policy inputs. `supported` describes observed implementation support only; it does not activate that support or authorize its use.

## Freshness, ordering, and hard limits

All comparisons use the ingress host's monotonic receipt event paired with its UTC clock. Implementations MUST apply these v1 constants exactly:

| Rule | v1 hard limit |
|---|---:|
| Maximum encoded wire document | 16,384 UTF-8 bytes |
| Maximum observation age at receipt | 15,000 ms |
| Maximum future clock skew | 5,000 ms |
| Maximum `expires_at - observed_at` | 15,000 ms |
| Maximum labels | 16 |
| Maximum label key | 32 UTF-8 bytes |
| Maximum label value | 64 UTF-8 bytes |
| Maximum capabilities | 16 |

The schema's character limits are an early structural check. The ingress adapter MUST additionally enforce the UTF-8 byte limits before parsing or persistence.

An observation is usable only when all of the following hold:

1. `observed_at <= received_at + 5,000 ms`.
2. `received_at - observed_at <= 15,000 ms`.
3. `observed_at < expires_at`.
4. `expires_at - observed_at <= 15,000 ms`.
5. `received_at < expires_at`.
6. `sequence` is strictly greater than the last accepted sequence for `(reporter_id, boot_id, canonical scope)`.

A new `boot_id` starts a new sequence domain but does not bypass authentication or freshness. A regression within the same domain is rejected as replay. State used for the comparison must be hub-owned and safe across concurrent ingress handlers.

Missing signals are not zero. A structurally valid but stale observation contributes `unknown` for every capability and signal. The last known value may be retained for diagnostics with its timestamp, but it MUST NOT remain a usable health, placement, admission, limit, or quota input.

## Failure behavior

| Condition | Result |
|---|---|
| Valid schema, authentication, signature, freshness, and sequence | Accept as short-lived observation |
| Missing signal or capability | Accept document; project that field as `unknown` |
| Stale or excessive future skew | Do not use; project observation fields as `unknown` |
| Sequence regression | Reject as replay; retain previous accepted state |
| TLS, mTLS, key, or signature failure | Reject; do not persist as accepted evidence |
| Unknown capability, wrong source/unit, oversized field, or extra field | Reject schema/ingress validation |
| Unproved node binding | Reject the node claim; never infer a node ID |

Every rejection and every transition to `unknown` must be auditable without logging certificate secrets, signatures, or user media.

## Fixture format

Files under `tests/fixtures/webrtc/sfu_node_observation/` use this test-only wrapper:

```json
{
  "fixture_version": "1",
  "case": "stable-case-name",
  "context": {
    "received_at": "2026-01-15T12:00:10Z",
    "transport": {},
    "signature_valid": true
  },
  "expected": {
    "schema_valid": true,
    "accepted": true,
    "projection": "usable",
    "reason": "accepted"
  },
  "document": {}
}
```

Stateful fixtures may add `last_accepted_sequence`. Consumers validate only `document` against the JSON Schema, then apply the semantic ingress checks using `context`.
