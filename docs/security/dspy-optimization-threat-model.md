# DSPy optimization threat model

| Threat | Control | Detection / recovery | Residual risk |
| --- | --- | --- | --- |
| Prompt or dataset poisoning | manifests, disjoint holdout, deterministic gates, closed schemas | score/policy regression blocks candidate | a permitted label can still be low quality |
| Secret exfiltration | redaction before worker, no raw logs, no free tools/endpoints | security tests and digest-only traces | external model receives policy-approved content |
| Cross-tenant cache/artifact leak | tenant in cache/artifact identity, server-derived paths | scope mismatch rejection | Hub compromise is outside worker containment |
| SSRF/endpoint rebinding | exact Hub `ProviderExecutionBinding`; no program endpoint fields | binding and endpoint-policy rejection | authorized endpoint remains trusted infrastructure |
| Metric gaming | deterministic gates first, holdout, minimum sample | non-comparable or red metrics block | semantic metrics retain model uncertainty |
| Pickle/code execution | JSON-only serializer, forbidden-import detector, no tools/eval/exec | static and malicious-payload tests | optional upstream dependency still needs scanning |
| Budget bypass | atomic call reservation, missing usage fail-closed, fixed concurrency | stable budget reason codes | provider accounting may be delayed |
| Stale worker/result | Hub attempt ID, HMAC authorization, monotone revision | stale attempt/finalization rejection | signing-key compromise defeats fencing |
| Promotion abuse | Hub-only registry, HMAC-attested program-bound evaluation, expected revision, rollback history | attestation/conflict/gate rejection | policy configuration is privileged |

Optimizer-generated instructions and demonstrations are untrusted content. They
cannot select providers, URLs, files, tools, retrievers, policies or promotion.
Default artifacts contain no raw provider errors, credentials, authorization
headers or unrestricted source content.
