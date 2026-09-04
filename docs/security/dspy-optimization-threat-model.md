# DSPy optimization threat model

The Hub is the only control-plane trust root. Its queue carries one closed,
signed assignment to one disposable DSPy Worker; the Worker cannot enqueue or
delegate. Provider and CodeCompass retriever are separate outbound trust
boundaries reached only through Hub-issued bindings. Artifact storage accepts
only canonical JSON under server-derived tenant paths. The API and Angular UI
are untrusted clients of the Hub policy boundary, and external model output is
always untrusted content rather than authority.

| Threat | Control | Detection / recovery | Residual risk |
| --- | --- | --- | --- |
| Prompt or dataset poisoning | manifests, disjoint holdout, deterministic gates, closed schemas | score/policy regression blocks candidate | a permitted label can still be low quality |
| Secret exfiltration | redaction before worker, no raw logs, no free tools/endpoints | security tests and digest-only traces | external model receives policy-approved content |
| Cross-tenant cache/artifact leak | tenant in cache/artifact identity, server-derived paths | scope mismatch rejection | Hub compromise is outside worker containment |
| Pickle execution through DSPy/DiskCache (CVE-2025-69872) | persistent DSPy disk cache is unconditionally disabled; bounded process-local memory cache only | adapter security-policy regression test and Hub local gate | compromised worker process remains inside container boundary |
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
Prompt/output bodies are transient Worker inputs and are excluded from default
logs and metrics; only digest and byte length cross the telemetry boundary.
Human-readable read models use `tenant_operators`, active runtime artifacts use
`promotion_runtime`, and both are retention-bound unless legal hold or an active
promotion reference prevents deletion.

## Ownership and automated negative gates

| Boundary | Owner | Required automatic test |
| --- | --- | --- |
| Dataset admission and source allowlist | Hub data policy | split leakage, secret/PII, oversize, unknown source |
| Provider and endpoint binding | Hub routing policy | unresolved model, endpoint rebinding, role mismatch, replay |
| Retrieval scope and content | Hub scope / Worker adapter | cross-tenant source, digest drift, backend loss, budget |
| DSPy state projection | Worker adapter | pickle, class/tool/path, unknown module, oversized state |
| Artifact retention | Hub artifact registry | traversal, symlink, digest drift, legal hold, active promotion |
| Evaluation and rollout | Hub evaluation policy | NaN, incomparable set, red deterministic gate, stale revision, stop reason |
| Container and dependency set | Release pipeline | non-root/read-only/cap-drop, lock hash, SBOM, advisory scan |

Optimizer-generated text is never interpreted as a policy, endpoint, tool,
module import, file path or approval. Provider and retrieval failures are
mapped to bounded reason codes before leaving the Worker. Automated gates fail
terminally and never ask a person to unblock a test or bounded run.
