# Privacy-safe semantic media observability

Semantic media telemetry is content-free. `agent.services.semantic_media_observability_policy` is the allowlist contract for events, traces, audits and metric labels. Unknown event types, fields, reason codes, composite values and oversized strings are rejected before emission.

Allowed events report only bounded operational state such as direction, transport kind, operation, worker kind, state, duration, count and an ephemeral scope digest. Each event rule fixes serialized size and label-cardinality limits. Public reason codes are stable enums; exception messages are not labels.

Audio, image pixels, transcript text, embeddings/residual features, payloads, keys, secrets, tokens, local paths, speakers, peers and partner identities are forbidden. Debug mode does not relax this rule. Content may only live in its separately authorized encrypted/domain store.

Scope digests use HMAC-SHA-256 with an operator secret and a time epoch, are truncated for bounded cardinality and rotate each epoch. The plaintext scope never appears in telemetry, and different scopes do not share a digest. Production deployments must rotate the digest secret and keep it outside application logs.
