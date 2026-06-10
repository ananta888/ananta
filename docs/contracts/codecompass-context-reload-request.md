# CodeCompass context_reload_request contract

This is the wire-format contract for `context_reload_request` payloads sent
from agents to the Ananta Hub when the supplied CodeCompass context is
insufficient to answer the user's question.

## Schema name

`context_reload_request.v1`

## Top-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | string | yes | Must be exactly `context_reload_request` |
| `reason` | string | yes | Free-text justification; max 500 chars; non-empty |
| `requested_context` | array | yes | 1..MAX entries; see below |
| `risk` | string | yes | Must be exactly `read_only` (default and enforced) |

Any payload that does not match the top-level shape is rejected with
`invalid_request_shape`. Any payload where `risk != "read_only"` is rejected
with `policy_blocked` and a 409 HTTP status.

## `requested_context` entries

Each entry is a single object with a `type` discriminator. The Hub limits
the total number of entries per request to **10** and deduplicates by the
`(type, query-or-path)` tuple.

### `file_range`

```json
{
  "type": "file_range",
  "path": "src/main/java/example/UserService.java",
  "start_line": 1,
  "end_line": 80
}
```

- `path` is repository-relative; absolute paths are rejected with
  `absolute_path_not_allowed`.
- `start_line` and `end_line` are 1-indexed and inclusive; `start_line` must
  be ≤ `end_line`.
- If the file does not exist in the materialized index, the entry is
  dropped with a per-entry `file_not_found` warning (not a request-level
  failure).

### `symbol`

```json
{
  "type": "symbol",
  "query": "PriceFieldPolicy"
}
```

- `query` is matched against the graph's `by_name` index first, then via
  FTS fallback.
- Ambiguous symbols (multiple matches) yield multiple delivered chunks, not
  an error.

### `codecompass_search`

```json
{
  "type": "codecompass_search",
  "query": "permission check for price field"
}
```

- `query` is a free-text query against the CodeCompass FTS store.
- Returned chunks are limited to the standard retrieval limit (configurable
  in `agent/config.py`).

### `graph_expand`

```json
{
  "type": "graph_expand",
  "seed": "UserService",
  "depth": 2,
  "direction": "outgoing"
}
```

- `seed` is resolved via the standard graph seed resolution (see
  `codecompass_architecture_query.resolve_seed`).
- `depth` defaults to the configuration's `default_depth`; clamped to
  `max_depth`.
- `direction` is one of `outgoing`, `incoming`, `both`; defaults to
  `outgoing`.

### `architecture_query`

```json
{
  "type": "architecture_query",
  "query_type": "field-policy-impact",
  "seed": "UserDto",
  "field": "price"
}
```

- `query_type` is one of the four whitelisted types
  (`dto-impact`, `controller-test-coverage`, `field-policy-impact`,
  `service-dependency-chain`).
- Unknown `query_type` is rejected with `invalid_query_type`.
- The result format is `codecompass_architecture_query_result.v1`.

## Response schema

The Hub returns a `context_reload_response.v1` payload:

```json
{
  "schema": "context_reload_response.v1",
  "status": "ok | policy_blocked | invalid_request_shape | invalid_query_type",
  "code": "policy_blocked",
  "delivered": [ /* array of chunks, possibly empty */ ],
  "warnings": [ /* per-entry warnings, e.g. file_not_found */ ]
}
```

The response is always returned (200 with `status` field set), except for
the `policy_blocked` case which returns HTTP 409 with the same body shape.

## Limits and dedup

- Maximum entries: **10**.
- Dedup key per entry: `(type, query-or-path-or-seed)`.
- Exceeding the cap silently clamps to the first 10 unique entries; a
  warning `entries_clamped_to_max` is attached to the response.
- `risk: read_only` is the only accepted value. Any other value triggers
  `policy_blocked` without dispatching the request.

## Examples

### Minimal valid request

```json
{
  "kind": "context_reload_request",
  "reason": "model answer cited no evidence for field X protection",
  "requested_context": [
    {"type": "symbol", "query": "PriceFieldPolicy"}
  ],
  "risk": "read_only"
}
```

### Multi-entry request with policy-impact query

```json
{
  "kind": "context_reload_request",
  "reason": "controller test coverage unclear from supplied context",
  "requested_context": [
    {"type": "architecture_query", "query_type": "controller-test-coverage", "seed": "UserController"},
    {"type": "file_range", "path": "src/test/java/example/UserControllerTest.java", "start_line": 1, "end_line": 200}
  ],
  "risk": "read_only"
}
```

### Rejected (mutating) request

```json
{
  "kind": "context_reload_request",
  "reason": "delete the field",
  "requested_context": [{"type": "file_range", "path": "x.java", "start_line": 1, "end_line": 2}],
  "risk": "write"
}
```

Response: HTTP 409, body `{"status": "policy_blocked", "code": "policy_blocked", ...}`.
