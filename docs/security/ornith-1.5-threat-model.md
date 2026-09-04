# Ornith 1.5 threat model

Model weights, GGUF metadata, chat templates, processors, images, prompts and
responses are untrusted inputs. Threats include digest substitution, malicious
pickle or executable remote code, template injection, path/symlink escape,
credential disclosure, prompt-injected tool use, denial of service through
context or parallelism, telemetry egress and compromised community builds.

Controls are split by responsibility. The Hub admits exact sources, policy,
tenant, task, assignment and dispatch bindings. A worker may materialize only
that closed assignment and cannot promote evidence. The immutable importer
allows pinned GGUF/safetensors and inert config inputs, rejects traversal,
symlinks, executables, remote code, size/digest mismatch and implicit network
access, then publishes read-only.

Runtime policy is in `config/security/ornith-runtime-policy.v1.json`: default
deny egress, no Docker socket, no privileged mode, read-only mounts, bounded
scratch, no telemetry, one request and resource reserve. Responses never
authorize actions. Native tool calls still pass Hub schema, tenant, approval
policy and operation-ID gates; free-form tool markup fails closed.

Unknown license, critical runtime CVEs, missing SBOM/image digest, artifact
mutation or absent production-scoped Hub evidence prevents production release.
Synthetic tests can exercise every path but cannot satisfy that release gate.
