# SFU Fanout Traffic Projection Contract

## Purpose

This contract projects the closed parent DataChannel message catalog onto SFU
route classes. The hub owns the policy decision. An SFU or worker may execute an
already authorized route, but it does not select a route class and does not
orchestrate another worker.

The implementation is a pure policy lookup. Its inputs are the exact parent
schema version and exact parent message kind. Message payloads, ciphertext and
domain objects are not inputs and must not be inspected to refine a decision.

## Parent contract binding

Policy version `ananta.sfu-fanout-traffic-projection.v1` is bound to:

| Parent property | Required value |
| --- | --- |
| Schema ID | `https://ananta.local/schemas/webrtc/datachannel_message.v1.json` |
| Schema version | `ananta.webrtc-datachannel.v1` |
| Message-kind field | `traffic_class` |

The parent registry contains exactly `control`, `transcript`,
`audio_recovery`, `visual_semantic`, `evidence_bulk` and `diagnostic`. This
policy does not create aliases or additional parent kinds.

## Closed projection matrix

| Parent kind | Privacy scope | Route class | Constraint |
| --- | --- | --- | --- |
| `control` | `authorized_group` | `shared` | Only the authorized group audience may receive it. |
| `transcript` | `authorized_group` | `shared` | Only the authorized group audience may receive it. |
| `audio_recovery` | `authorized_sender_receiver_pair` | `pair_private` | Raw or delayed audio stays on an authorized pair route. |
| `visual_semantic` | `authorized_receiver` | `receiver_private` | The parent class can carry receiver-specific residuals, so it is never shared. |
| `evidence_bulk` | `sfu_forbidden` | `forbidden_for_sfu` | Evidence is excluded from SFU transport. |
| `diagnostic` | `authorized_receiver` | `receiver_private` | Diagnostics are scoped to one authorized receiver. |

Every known kind occurs exactly once. The four route classes are disjoint:

| Route class | Meaning |
| --- | --- |
| `shared` | One authorized group route may be used. |
| `receiver_private` | A separately authorized route for one receiver is required. |
| `pair_private` | A separately authorized sender-receiver pair route is required. |
| `forbidden_for_sfu` | No SFU route may be created. |

The projection is an upper bound, not an authorization grant. Audience,
consent, epoch, expiry and E2EE checks remain mandatory before an allowed route
can be executed.

## Protected content invariants

Evidence and raw-audio evidence are not shared. Registered `evidence_bulk` is
forbidden and registered `audio_recovery` is pair-private.

Receiver-specific visual residuals are not shared. Because the parent catalog
does not expose a separate top-level residual kind, all `visual_semantic`
traffic is conservatively receiver-private. The hub must not inspect a visual
payload to distinguish a scene from a residual.

Speaker embeddings, datasets, adapters, training inventory and private
annotations are not registered parent message kinds. Any attempt to introduce
such a kind is handled by the unknown-kind rule and is
`forbidden_for_sfu`. Producers must not relabel protected content as `control`
or `transcript`; that is a parent-contract violation, not a signal for payload
inspection in this service.

## Fail-closed behavior

An exact lookup is mandatory. Case folding, trimming, aliases and legacy
fallbacks are prohibited.

An unknown or newly added parent kind returns `forbidden_for_sfu` with reason
`unknown_parent_message_kind`. A known kind presented with any other schema
version returns `forbidden_for_sfu` with reason
`unsupported_parent_schema_version`. Invalid non-string identifiers are handled
the same way and never invoke user-defined string conversion.

A new kind can become routable only through a versioned update of the parent
schema, this policy, its privacy review and the complete matrix test. A config
whose unknown-kind default is less restrictive than `forbidden_for_sfu` is
rejected at load time.

## Service boundary

`SfuFanoutTrafficProjectionService` depends on the immutable
`SfuFanoutTrafficProjectionPolicy` abstraction. JSON loading and validation are
kept in a separate loader function. The service returns a decision and performs
no network, queue, persistence or routing side effect.

This separation protects SRP and DIP: configuration loading is an infrastructure
concern, while the service is deterministic business policy that can be tested
with an injected policy. The narrow two-field input also protects ISP and makes
payload inspection structurally unavailable.

## Evidence and activation boundary

This contract does not assert runtime activation readiness and does not invent
`SRC_*` or `RUN_*` evidence identifiers. Parent `no_go` or `observe_only`
decisions remain fail-closed and take precedence over every projection returned
by this service.
