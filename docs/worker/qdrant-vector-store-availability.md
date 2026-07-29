# Qdrant availability and JSON fallback

Availability policy is part of the immutable resolved VectorStore
configuration. CodeCompass and Wiki use the same `VectorStoreFactory`
composition, so the policy has identical semantics in both domains.

## Read behavior

`availability.on_unavailable` accepts exactly:

- `fail_fast`: a Qdrant timeout or unavailable result raises the original
  bounded `VectorStoreError`;
- `degraded_empty`: the read returns no hits with degraded diagnostics and
  continues no further fallback;
- `explicit_json_fallback`: a configured JSON store may answer the read only
  after its compatibility contract exactly matches the query.

Fallback is limited to read-only search and only to `qdrant_unavailable` or
`qdrant_timeout`. Authorization failures, schema incompatibility and all other
errors never select JSON. In particular, `qdrant_unauthorized`,
`incompatible_collection` and `vector_store_compatibility_required` always
raise a bounded `VectorStoreError`, regardless of whether the configured mode
is `fail_fast`, `degraded_empty` or `explicit_json_fallback`. Product callers
must not convert these trust or compatibility failures into an empty result.
An absent fallback yields `fallback_not_configured`; stale or incompatible
JSON yields `fallback_state_incompatible`.

Diagnostics always report `requested_backend`, `effective_backend` and
`provider_fallback`. They use bounded reason codes and contain no scope IDs,
paths, endpoint URLs, payload text or secrets.

## Mutation behavior

Every write—rebuild, refresh, upsert, delete and scope delete—always targets
the primary store. The availability decorator never redirects a mutation to
JSON. Qdrant mutations remain Hub-owned tasks and fail closed at the delegated
Worker when the primary is unavailable.

This separation prevents a degraded read policy from silently splitting
authoritative index state between backends.
