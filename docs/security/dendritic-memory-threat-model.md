# Dendritic-memory experiment threat model

External terminology and performance claims are unverified inspiration, not
architecture evidence. Memory Packs are treated as hostile adapter-like input.

| Threat | Control | Negative gate / recovery |
| --- | --- | --- |
| Dataset poisoning or leakage | immutable dataset/split digests; duplicate, paraphrase, OOD and canary checks | evaluation remains ineligible |
| Cross-tenant disclosure | server-derived tenant scope in jobs, registry and artifact paths | foreign identifiers return not-found |
| Stale or forged Worker | attempt ID, HMAC job capability and immutable revisions | reject stale/invalid transition |
| Artifact manipulation | Safetensors-only executable packs, exact file allowlist, size and SHA-256 checks | quarantine/reject; never activate |
| Pickle/code execution | no pickle/cloudpickle/dill; import-boundary detector; no free class/import/config strings | static and malicious-payload tests fail |
| Path traversal or symlink swap | server-derived resolved paths, identifier grammar, symlink rejection and atomic writes | artifact ingestion aborts |
| Archive/zip bomb | v1 accepts no archive format and at most two individually bounded files | unknown file or oversized payload is rejected before persistence |
| Hidden capability activation | separate registry, experimental-only state and default-off runtime policy | LoRA approval cannot activate a pack |
| Pack stacking conflict | ordered parent digests, identical base snapshot/architecture and disjoint targets | composition rejected atomically |
| Metric gaming | matched LoRA parameter budget, three seeds, Base/LoRA/experiment comparison and HMAC-attested result | registry approval fails closed |
| Resource exhaustion | bounded branches, dimensions, steps, pack bytes and active pack count | admission or runtime gate rejects |

Raw examples, secrets, prompts and model outputs never enter standard events or
audit records. Release evidence accepts only exact assignment-provided
`SRC_*` and `RUN_*` identifiers; missing or unknown identifiers remain failed.
