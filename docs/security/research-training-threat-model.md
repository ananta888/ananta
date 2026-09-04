# Research-training threat model

Protected assets are Hub orchestration authority, tenant/project boundaries, budgets and quota, curated datasets,
checkpoints, evidence identities, evaluation results and production routes. API payloads, staged data, Worker
inventory, model output, result files, artifact bytes and identifier strings are untrusted.

Controls:

- closed schemas and semantic contracts reject unknown fields, cycles, unsupported inputs, stale leases and drift;
- only the Hub admits sources, reserves runs, chooses Workers and releases DAG stages;
- authenticated Worker identity is bound to the stored assignment and cannot be overridden by request fields;
- input files are stable-read, size/digest checked and relative to an immutable workspace;
- checkpoints use Safetensors, never pickle; executable ingress is denied and writes are atomic;
- the Worker container is non-root, read-only, capability-free and offline with bounded resources;
- generated-code evaluation uses a separate digest-pinned, no-network, read-only container and bounded argv;
- lineage parents must exist, quotas are reserved before writes, and garbage collection cannot remove parents/pins;
- metrics are content-free, allowlisted and sequence-fenced;
- promotion requires attestation, critical per-task gates and Hub Registry verification of exact source/run bindings.

Residual risks: SQLite limits the experimental topology to a single Hub writer; CPU gates do not prove a particular
GPU/driver/CUDA combination; simplistic regex scanning complements but does not replace organizational data review.
Those profiles remain machine-readable unverified until their own automatic execution evidence exists. Human review
may add governance context but cannot bypass a mandatory gate.
