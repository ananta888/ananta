# SFU broadcast contract parity

The Python and Angular validators consume the same versioned local corpus.
The parity runner executes both suites with bounded CPU, memory and time,
requires zero failed, skipped or pending cases, and binds its report to the
corpus plus every referenced schema and fixture.

Duplicate JSON properties are rejected before schema validation, semantic
rules, trust verification, storage or UI binding with the stable reason code
contract_duplicate_json_key. Escaped and nested duplicates are covered by the
materializer and deterministic fuzz cases.

Receiver-group member digests are authoritative only at the Hub. Production
Python validation receives a metadata repository and KMS-backed digest service.
Angular requires an explicit verifier callback and fails closed without one.
The legacy v1 hex-key adapter can only be constructed for test or development;
production construction is rejected.
